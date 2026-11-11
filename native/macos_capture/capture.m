// Native, input-only Core Audio process tap. No screen/microphone capture or playback.
#import <Foundation/Foundation.h>
#import <CoreAudio/CoreAudio.h>
#import <CoreAudio/CATapDescription.h>
#import <CoreAudio/AudioHardwareTapping.h>
#include <stdatomic.h>
#include <signal.h>
#include <unistd.h>
#include <errno.h>
#include <math.h>

#define CAPACITY 65536u
static float ring[CAPACITY * 2];
static _Atomic uint64_t produced, consumed;
static _Atomic uint32_t dropped;
static volatile sig_atomic_t stopping;
static void stop_signal(int sig) { stopping = 1; }

static OSStatus capture_input(AudioObjectID device, const AudioTimeStamp *now,
    const AudioBufferList *input, const AudioTimeStamp *inputTime,
    AudioBufferList *output, const AudioTimeStamp *outputTime, void *context) {
    // Tap-only aggregate has no hardware outputs; never send captured data out.
    if (!input || !input->mNumberBuffers) return noErr;
    const AudioBuffer *first = &input->mBuffers[0];
    if (!first->mData || !first->mNumberChannels) return noErr;
    uint32_t frames = first->mDataByteSize / (sizeof(float)*first->mNumberChannels);
    uint32_t channels = 0;
    for (UInt32 b=0;b<input->mNumberBuffers;b++) {
        const AudioBuffer *buf = &input->mBuffers[b];
        if (!buf->mData || !buf->mNumberChannels) return noErr;
        uint32_t available = buf->mDataByteSize / (sizeof(float)*buf->mNumberChannels);
        if (available < frames) frames = available;
        channels += buf->mNumberChannels;
    }
    if (channels != 2) { atomic_fetch_add(&dropped,1); return noErr; }
    uint64_t end = atomic_load_explicit(&produced,memory_order_relaxed);
    uint64_t begin = atomic_load_explicit(&consumed,memory_order_acquire);
    if (frames > CAPACITY-(end-begin)) { atomic_fetch_add(&dropped,1); return noErr; }
    for (uint32_t f=0;f<frames;f++) {
        uint32_t ch = 0;
        for (UInt32 b=0;b<input->mNumberBuffers;b++) {
            const AudioBuffer *buf = &input->mBuffers[b];
            const float *data = buf->mData;
            for (UInt32 c=0;c<buf->mNumberChannels;c++) {
                ring[((end+f)%CAPACITY)*2+ch++] = data[f*buf->mNumberChannels+c];
            }
        }
    }
    atomic_store_explicit(&produced,end+frames,memory_order_release);
    return noErr;
}

static BOOL write_all(const void *data, size_t count) {
    const char *bytes = data;
    while (count && !stopping) {
        ssize_t n = write(STDOUT_FILENO,bytes,count);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return NO;
        bytes += n; count -= n;
    }
    return count == 0;
}
static BOOL packet(uint32_t rate,uint32_t frames) {
    struct { char magic[4]; uint32_t frames,rate,channels,drops; } header =
        {{'O','S','C','A'},frames,rate,2,atomic_load(&dropped)};
    _Static_assert(sizeof(header)==20,"Unexpected wire header layout");
    if (!write_all(&header,sizeof(header))) return NO;
    uint64_t begin = atomic_load_explicit(&consumed,memory_order_relaxed);
    uint32_t first = MIN(frames,CAPACITY-(begin%CAPACITY));
    if (!write_all(&ring[(begin%CAPACITY)*2],first*2*sizeof(float))) return NO;
    if (frames>first && !write_all(ring,(frames-first)*2*sizeof(float))) return NO;
    atomic_store_explicit(&consumed,begin+frames,memory_order_release);
    return YES;
}
static AudioObjectPropertyAddress address(AudioObjectPropertySelector selector) {
    return (AudioObjectPropertyAddress){selector,kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
}
static Float64 nominal_rate(AudioObjectID device) {
    Float64 rate=0; UInt32 size=sizeof(rate);
    AudioObjectPropertyAddress prop=address(kAudioDevicePropertyNominalSampleRate);
    return AudioObjectGetPropertyData(device,&prop,0,NULL,&size,&rate) ? 0 : rate;
}
static AudioObjectID default_output(void) {
    AudioObjectID device=0; UInt32 size=sizeof(device);
    AudioObjectPropertyAddress prop=address(kAudioHardwarePropertyDefaultOutputDevice);
    return AudioObjectGetPropertyData(kAudioObjectSystemObject,&prop,0,NULL,&size,&device) ? 0 : device;
}
static NSString *device_uid(AudioObjectID device) {
    CFStringRef uid=NULL; UInt32 size=sizeof(uid);
    AudioObjectPropertyAddress prop=address(kAudioDevicePropertyDeviceUID);
    if (!device || AudioObjectGetPropertyData(device,&prop,0,NULL,&size,&uid) || !uid) return nil;
    return (__bridge_transfer NSString*)uid;
}
static BOOL stereo_float(AudioStreamBasicDescription format) {
    return format.mFormatID==kAudioFormatLinearPCM && (format.mFormatFlags&kAudioFormatFlagIsFloat) &&
        format.mBitsPerChannel==32 && format.mChannelsPerFrame==2 &&
        isfinite(format.mSampleRate) && format.mSampleRate>=8000 && format.mSampleRate<=384000;
}
static BOOL check(OSStatus status, const char *operation) {
    if (!status) return YES;
    fprintf(stderr,"%s failed (Core Audio status %d). Check system-audio access and restart capture from the macOS desktop.\n",operation,(int)status);
    return NO;
}

int main(int argc,const char **argv) { @autoreleasepool {
    signal(SIGTERM,stop_signal); signal(SIGINT,stop_signal); signal(SIGPIPE,SIG_IGN);
    if (argc>1 && !strcmp(argv[1],"--self-test")) {
        // Test real callback layout conversion and framed pipe output, without TCC/devices.
        float left[2]={.125f,-.5f},right[2]={-.25f,.75f};
        struct { UInt32 count; AudioBuffer buffers[2]; } data =
            {2,{{1,sizeof(left),left},{1,sizeof(right),right}}};
        capture_input(0,NULL,(AudioBufferList*)&data,NULL,NULL,NULL,NULL);
        float packed[4]={.25f,-.75f,1.25f,-1.5f};
        AudioBufferList interleaved={1,{{2,sizeof(packed),packed}}};
        capture_input(0,NULL,&interleaved,NULL,NULL,NULL,NULL);
        return packet(48000,4)?0:1;
    }
    BOOL describe = argc>1 && !strcmp(argv[1],"--describe");
    if (@available(macOS 14.2,*)) {
        AudioObjectID tap=0,aggregate=0; AudioDeviceIOProcID proc=NULL;
        BOOL started=NO; int result=1; NSDictionary *spec=nil;
        NSMutableArray<NSNumber*> *excluded=[NSMutableArray array];
        for (int index=0;index<2;index++) {
            pid_t pid=index?getppid():getpid(); AudioObjectID process=0; UInt32 size=sizeof(process);
            AudioObjectPropertyAddress prop=address(kAudioHardwarePropertyTranslatePIDToProcessObject);
            if (!AudioObjectGetPropertyData(kAudioObjectSystemObject,&prop,sizeof(pid),&pid,&size,&process) && process)
                [excluded addObject:@(process)];
        }
        // An unmixed tap on the default output device's first stream takes that stream's
        // format, so it runs at the device rate (96 kHz included). Global and mixdown taps
        // mix at 48 kHz; they remain the fallback, for example for multichannel streams.
        // `--describe` prints the chosen tap and rates and counts frames for 2 s.
        AudioObjectID output=default_output(); NSString *output_uid=device_uid(output);
        CATapDescription *description=nil; AudioStreamBasicDescription format={0};
        AudioObjectPropertyAddress prop=address(kAudioTapPropertyFormat);
        const char *kind="global";
        for (int attempt=output_uid?0:1;attempt<2 && !tap;attempt++) {
            description=attempt ? [[CATapDescription alloc] initStereoGlobalTapButExcludeProcesses:excluded]
                : [[CATapDescription alloc] initExcludingProcesses:excluded andDeviceUID:output_uid withStream:0];
            if (!attempt) { description.mixdown=NO; description.mono=NO; }
            description.name=@"phosphor System Audio";
            description.privateTap=YES; description.muteBehavior=CATapUnmuted;
            description.UUID=[NSUUID UUID];
            OSStatus created=AudioHardwareCreateProcessTap(description,&tap);
            if (created || !tap) {
                if (attempt) { check(created,"Create system-audio tap"); goto cleanup; }
                tap=0; continue;
            }
            UInt32 size=sizeof(format);
            if (AudioObjectGetPropertyData(tap,&prop,0,NULL,&size,&format) || !stereo_float(format)) {
                if (attempt) {
                    fprintf(stderr,"Unsupported system-audio tap format; expected native float32 stereo PCM.\n"); goto cleanup;
                }
                AudioHardwareDestroyProcessTap(tap); tap=0; continue;
            }
            kind=attempt?"global":"output device";
        }
        if (!tap) { fprintf(stderr,"Core Audio returned no tap object. Run from a normal macOS desktop session.\n"); goto cleanup; }
        spec=@{
            @kAudioAggregateDeviceNameKey:@"phosphor Capture (private)",
            @kAudioAggregateDeviceUIDKey:[NSUUID UUID].UUIDString,
            @kAudioAggregateDeviceIsPrivateKey:@YES,
            @kAudioAggregateDeviceTapAutoStartKey:@NO,
            @kAudioAggregateDeviceTapListKey:@[@{
                @kAudioSubTapUIDKey:description.UUID.UUIDString,
                @kAudioSubTapDriftCompensationKey:@YES}]
        };
        if (!check(AudioHardwareCreateAggregateDevice((__bridge CFDictionaryRef)spec,&aggregate),"Create private capture device")) goto cleanup;
        if (!check(AudioDeviceCreateIOProcID(aggregate,capture_input,NULL,&proc),"Create input callback")) goto cleanup;
        // A tap-only capture device starts at its own default rate; run it at the tap's
        // rate so Core Audio does not resample the captured mix.
        Float64 tap_rate=format.mSampleRate, aggregate_before=nominal_rate(aggregate);
        if (aggregate_before!=tap_rate) {
            AudioObjectPropertyAddress rate_prop=address(kAudioDevicePropertyNominalSampleRate);
            AudioObjectSetPropertyData(aggregate,&rate_prop,0,NULL,sizeof(tap_rate),&tap_rate);
            for (int wait=0;wait<50 && nominal_rate(aggregate)!=tap_rate;wait++) usleep(10000);
        }
        // Report the rate the callback is actually delivered at.
        Float64 capture_rate=nominal_rate(aggregate);
        if (!(capture_rate>=8000 && capture_rate<=384000)) capture_rate=tap_rate;
        if (describe)
            fprintf(stdout,"tap=%s output_device_rate=%.0f tap_format_rate=%.0f capture_device_rate_before=%.0f capture_rate=%.0f\n",
                    kind,nominal_rate(output),tap_rate,aggregate_before,capture_rate);
        if (!check(AudioDeviceStart(aggregate,proc),"Start system-audio capture")) goto cleanup;
        started=YES;
        if (describe) {
            // Count delivered frames only; samples are discarded, never written out.
            uint64_t first=0,last=0; NSDate *begin=nil;
            for (int tick=0;tick<2500;tick++) {
                usleep(1000); last=atomic_load(&produced); atomic_store(&consumed,last);
                if (tick==499) { first=last; begin=[NSDate date]; }
            }
            fprintf(stdout,"measured_frames_per_second=%.0f drops=%u\n",(last-first)/-[begin timeIntervalSinceNow],atomic_load(&dropped));
            atomic_store(&consumed,last); result=0; goto cleanup;
        }
        uint32_t rate=(uint32_t)llround(capture_rate);
        if (!packet(rate,0)) goto cleanup; // Readiness, without waiting for sound.
        result=0;
        pid_t parent=getppid();
        while (!stopping && getppid()==parent) {
            uint64_t available=atomic_load_explicit(&produced,memory_order_acquire)-atomic_load_explicit(&consumed,memory_order_relaxed);
            if (available) { if (!packet(rate,(uint32_t)MIN(available,1024))) break; }
            else usleep(1000);
        }
    cleanup:
        if (started) AudioDeviceStop(aggregate,proc);
        if (proc) AudioDeviceDestroyIOProcID(aggregate,proc);
        if (aggregate) AudioHardwareDestroyAggregateDevice(aggregate);
        if (tap) AudioHardwareDestroyProcessTap(tap);
        return result;
    }
    fprintf(stderr,"System audio capture requires macOS 14.2 or later.\n");return 1;
}}
