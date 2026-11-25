"""Missing audio files and Collect media: grouping, locating, folder searches and copies (no Qt).

A missing file is identified by its clip's last known path and fingerprint (a split or
duplicated clip shares one file), so relinking a file fixes every clip that uses it.
Nothing here decodes audio: the window decodes located files on a worker thread.
"""
from dataclasses import dataclass, replace
import os
from pathlib import Path
import shutil
import uuid

import soundfile

from phosphor.engine.audio_clips import fingerprint, matches, media_folder, validate_audio_clip


def file_key(clip):
    """What identifies a clip's file: its last known path and fingerprint."""
    return clip.path, clip.fingerprint


def missing_files(project):
    """{file key: clip indices} for the missing audio files, in clip order."""
    files = {}
    for index, clip in enumerate(project.audio_clips):
        if clip.missing: files.setdefault(file_key(clip), []).append(index)
    return files


def missing_count(project):
    return len(missing_files(project))


def located_clip(clip, path):
    """The clip pointing at path. A file with the clip's fingerprint keeps everything else; any
    other file (a re-export, say) takes its name, size, fingerprint and length from the file, and
    the clip is shortened if the file is shorter. Raises ValueError for unreadable files or files
    that end before the clip's offset."""
    path = Path(path).resolve()
    if matches(clip, path): return replace(clip, path=str(path), missing=False)
    try:
        info = soundfile.info(str(path))
        if info.frames <= 0 or info.samplerate <= 0: raise ValueError('The audio file is empty.')
        size = path.stat().st_size
        print_ = fingerprint(path)
    except (OSError, RuntimeError) as error:
        raise ValueError(f'Could not read audio file: {error}') from error
    length = info.frames / info.samplerate
    if clip.offset >= length:
        raise ValueError(f'{path.name} is shorter than the start of the clip ({clip.offset:.2f} s into the file).')
    duration = min(clip.duration, length - clip.offset)
    located = replace(clip, path=str(path), name=path.name[:255], size=size, fingerprint=print_,
                      source_duration=length, duration=duration, missing=False,
                      fade_in=min(clip.fade_in, duration), fade_out=min(clip.fade_out, duration))
    return validate_audio_clip(located)


def relink(project, located):
    """Project with the files in `located` ({file key: path}) relinked; clips keep their places."""
    from phosphor.engine.composition import validate_project
    clips = list(project.audio_clips)
    for index, clip in enumerate(clips):
        path = located.get(file_key(clip))
        if path is not None: clips[index] = located_clip(clip, path)
    return validate_project(replace(project, audio_clips=tuple(clips)))


def find_in_folder(project, folder, keys=None):
    """{file key: path} for missing files (or only `keys`) found by name and fingerprint directly
    in folder: the follow-up after a file was located by hand."""
    found = {}
    for key, indices in missing_files(project).items():
        if keys is not None and key not in keys: continue
        clip = project.audio_clips[indices[0]]
        candidate = Path(folder) / clip.name
        if matches(clip, candidate): found[key] = candidate.resolve()
    return found


def search_folder(project, folder, stop=None):
    """{file key: path} for missing files found by name and fingerprint in folder and its
    subfolders (sorted, top-down: the first match wins). `stop()` may end the search early."""
    wanted = {}
    for key, indices in missing_files(project).items():
        wanted.setdefault(project.audio_clips[indices[0]].name.casefold(), []).append(key)
    found = {}
    for root, folders, files in os.walk(folder):
        folders.sort(); files.sort()
        for name in files:
            for key in wanted.get(name.casefold(), ()):
                if key in found: continue
                candidate = Path(root) / name
                clip = next(c for c in project.audio_clips if file_key(c) == key)
                if matches(clip, candidate): found[key] = candidate.resolve()
        if len(found) == sum(map(len, wanted.values())) or (stop is not None and stop()): break
    return found


@dataclass(frozen=True)
class CollectPlan:
    folder: Path  # The project's media folder.
    copies: tuple  # (source, destination) per file to copy.
    targets: dict  # {file key: destination} for every collected file (copied or already there).
    skipped: int  # Missing files, which cannot be collected.


def collect_plan(project, project_path):
    """Which used files to copy into `<project name> media` and where. Files already in the
    folder stay; a name taken by a different file gets a numbered name."""
    folder = media_folder(project_path)
    resolved_folder = folder.resolve()
    copies, targets, taken = [], {}, set()
    for clip in project.audio_clips:
        key = file_key(clip)
        if clip.missing or key in targets: continue
        source = Path(clip.path).resolve()
        if source.parent == resolved_folder:
            targets[key] = source; taken.add(source.name.casefold())
    for clip in project.audio_clips:
        key = file_key(clip)
        if clip.missing or key in targets: continue
        source = Path(clip.path).resolve()
        stem, suffix = Path(clip.name).stem or source.stem, Path(clip.name).suffix or source.suffix
        number = 1
        while True:
            name = f'{stem}{suffix}' if number == 1 else f'{stem} ({number}){suffix}'
            destination = folder / name
            if name.casefold() not in taken:
                if not destination.exists(): copies.append((source, destination)); break
                if matches(clip, destination): break  # The same file is already there.
            number += 1
        taken.add(name.casefold()); targets[key] = destination
    skipped = len(missing_files(project))
    return CollectPlan(folder, tuple(copies), targets, skipped)


def copy_media(plan, progress=None):
    """Copy the plan's files (each through a temporary name, so a failed copy leaves nothing
    half written). progress(done, total) is called after each file."""
    plan.folder.mkdir(parents=True, exist_ok=True)
    for done, (source, destination) in enumerate(plan.copies, 1):
        temporary = destination.with_name('.collect-' + uuid.uuid4().hex + destination.suffix)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.exists(): temporary.unlink()
        if progress is not None: progress(done, len(plan.copies))


def collected(project, plan):
    """Project with every collected file pointing into the media folder (fingerprints checked)."""
    from phosphor.engine.composition import validate_project
    clips = list(project.audio_clips)
    for index, clip in enumerate(clips):
        target = plan.targets.get(file_key(clip))
        if target is None or Path(clip.path) == target: continue
        if not matches(clip, target): raise ValueError(f'The copy of {clip.name} does not match the original.')
        clips[index] = replace(clip, path=str(Path(target).resolve()))
    return validate_project(replace(project, audio_clips=tuple(clips)))

