"""Run the smoke scripts in fresh processes and judge each one by its own exit code.

    python tools/run_smokes.py                      # every smoke at QT_SCALE_FACTOR 1 and 1.5
    python tools/run_smokes.py --only video_player --scale 1 --repeat 20
    python tools/run_smokes.py --only composer note_tools --retry
    python tools/run_smokes.py --headless           # offscreen Qt: not native evidence

Each smoke (tests/smoke/smoke_*.py) runs once per scale with the Python running this
script, from the app folder, with a per-smoke timeout. The exit code recorded is that
smoke process's own return code, read from its Popen object as soon as it ends: a
shell runner once logged `basename`'s exit code instead, and failing smokes looked
green (0.66.0). A smoke that prints its pass message and then crashes on shutdown fails.

The runner prints one line per run, the last lines of output for each failure and a
summary table, writes a JSON summary (name, scale, attempt, exit code, seconds, last 20
lines) and exits 1 if any run failed or timed out. --retry reruns a failed smoke once;
a smoke that passes only on the retry is reported as FLAKY and still fails the run, so
flaky smokes stay visible. Smokes run one at a time: they show windows and some need
keyboard focus, so do not use the desktop while they run.

Works the same on Windows and macOS. Each smoke gets its own empty user folder
(tests/smoke/_path.py), so the user's preferences are never read or written.
"""
import argparse
from datetime import datetime
import fnmatch
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

APP = Path(__file__).resolve().parent.parent
SMOKES = APP/'tests'/'smoke'
TAIL = 20


def discover(only=()):
    """Smoke scripts in name order, filtered by --only (substring or glob, any match)."""
    paths = sorted(SMOKES.glob('smoke_*.py'))
    if not only: return paths
    def wanted(stem):
        return any(fnmatch.fnmatch(stem, pattern) or fnmatch.fnmatch(stem, f'smoke_{pattern}')
                   if any(c in pattern for c in '*?[') else pattern in stem for pattern in only)
    return [path for path in paths if wanted(path.stem)]


def scale_text(scale):
    """QT_SCALE_FACTOR as typed (1, 1.5), not 1.0."""
    return f'{scale:g}'


def exit_text(code):
    """Exit code for people: Windows crash codes (0xC0000005) read better in hex."""
    if code is None: return 'timeout'
    if code < 0 and os.name != 'nt': return f'{code} (signal {-code})'
    if code > 0xFFFF or code < 0: return f'{code} (0x{code & 0xFFFFFFFF:08X})'
    return str(code)


def kill_tree(process):
    """Stop a timed-out smoke and any process it started (smoke_builder_quit starts one)."""
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try: process.kill()
    except OSError: pass


def run_one(path, scale, timeout, headless, extra):
    """Run one smoke; return (exit code or None on timeout, seconds, output text)."""
    env = dict(os.environ, QT_SCALE_FACTOR=scale_text(scale), PYTHONIOENCODING='utf-8',
               PYTHONUNBUFFERED='1')
    env.pop('PHOSPHOR_USER_DIR', None)  # _path.py gives every smoke a fresh one.
    arguments = [sys.executable, str(path), *extra]
    if headless:
        env['QT_QPA_PLATFORM'] = 'offscreen'
        arguments.append('--headless')
    start = time.perf_counter()
    process = subprocess.Popen(arguments, cwd=APP, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               start_new_session=os.name != 'nt')
    try:
        output, _ = process.communicate(timeout=timeout)
        code = process.returncode  # This smoke's own exit code, nothing else's.
    except subprocess.TimeoutExpired:
        kill_tree(process)
        try: output, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired: output = b'(no output: a child process kept the pipe open)'
        code = None
    seconds = time.perf_counter()-start
    return code, seconds, output.decode('utf-8', errors='replace')


def tail(text, lines=TAIL):
    return text.rstrip().splitlines()[-lines:]


def summary_table(results, scales):
    """One row per smoke, one column per scale: PASS, FLAKY, FAIL n or TIMEOUT, with seconds."""
    names = list(dict.fromkeys(result['name'] for result in results))
    cells = {}
    for result in results:
        key = result['name'], result['scale']
        cells.setdefault(key, []).append(result)
    def cell(runs):
        if not runs: return '-'
        finals = [run for run in runs if run['final']]
        passed = sum(run['status'] == 'PASS' for run in finals)
        flaky = sum(run['status'] == 'FLAKY' for run in finals)
        seconds = max(run['seconds'] for run in runs)
        if len(finals) > 1:  # --repeat: count the repetitions.
            text = f'{passed}/{len(finals)} pass' + (f', {flaky} flaky' if flaky else '')
        else:
            text = finals[0]['status'] if finals[0]['status'] != 'FAIL' else f"FAIL {exit_text(finals[0]['exit_code'])}"
        return f'{text} {seconds:5.1f}s'
    headers = ['smoke'] + [f'scale {scale_text(scale)}' for scale in scales]
    rows = [[name] + [cell(cells.get((name, scale_text(scale)), [])) for scale in scales] for name in names]
    widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))]
    line = lambda row: '  '.join(str(value).ljust(width) for value, width in zip(row, widths)).rstrip()
    return '\n'.join([line(headers), line(['-'*width for width in widths]), *map(line, rows)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--only', nargs='+', default=(), metavar='NAME',
                        help='run smokes whose name contains NAME (or matches a glob such as "composer*")')
    parser.add_argument('--scale', nargs='+', type=float, default=(1., 1.5), metavar='FACTOR',
                        help='QT_SCALE_FACTOR values (default: 1 1.5)')
    parser.add_argument('--timeout', type=float, default=300., metavar='SECONDS',
                        help='per-smoke timeout (default: 300)')
    parser.add_argument('--repeat', type=int, default=1, metavar='N',
                        help='run each smoke N times per scale (to prove a flaky smoke is fixed)')
    parser.add_argument('--retry', action='store_true',
                        help='rerun a failed smoke once; a pass on the retry is FLAKY and still fails the run')
    parser.add_argument('--headless', action='store_true',
                        help='offscreen Qt and --headless for every smoke (not native evidence)')
    parser.add_argument('--json', type=Path, metavar='PATH',
                        help='summary file (default: .user/smoke-runs/<time>.json in the app folder)')
    parser.add_argument('--list', action='store_true', help='list the selected smokes and exit')
    parser.add_argument('--verbose', action='store_true', help='print every smoke\'s whole output')
    arguments = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(errors='replace')
    paths = discover(arguments.only)
    if not paths:
        print('No smoke matches', ' '.join(arguments.only)); return 2
    if arguments.list:
        print('\n'.join(path.stem for path in paths)); return 0
    scales = list(dict.fromkeys(arguments.scale))
    total = len(paths)*len(scales)*arguments.repeat
    started = datetime.now()
    print(f'{total} runs: {len(paths)} smokes x scale {", ".join(map(scale_text, scales))}'
          + (f' x {arguments.repeat}' if arguments.repeat > 1 else '')
          + f' · Python {platform.python_version()} ({sys.executable})'
          + (' · HEADLESS (offscreen)' if arguments.headless else ''), flush=True)
    results, index = [], 0
    for scale in scales:
        for path in paths:
            for repetition in range(arguments.repeat):
                index += 1
                attempts = 2 if arguments.retry else 1
                for attempt in range(1, attempts+1):
                    code, seconds, output = run_one(path, scale, arguments.timeout, arguments.headless, ())
                    passed = code == 0
                    final = passed or attempt == attempts
                    status = 'PASS' if passed else 'TIMEOUT' if code is None else 'FAIL'
                    if passed and attempt > 1: status = 'FLAKY'
                    results.append(dict(name=path.stem, scale=scale_text(scale), repetition=repetition+1,
                                        attempt=attempt, final=final, status=status, exit_code=code,
                                        seconds=round(seconds, 2), tail=tail(output)))
                    label = f'[{index:>{len(str(total))}}/{total}] {path.stem} @{scale_text(scale)}'
                    if arguments.repeat > 1: label += f' #{repetition+1}'
                    if attempt > 1: label += ' (retry)'
                    print(f'{label:<52} {status:<7} exit {exit_text(code):<8} {seconds:6.1f}s', flush=True)
                    if arguments.verbose or (not passed and 'Smoke failed after a guarded paint exception' in output):
                        print(output.rstrip(), flush=True)  # The paint traceback can precede the last 20 lines.
                    elif not passed:
                        print('    ' + '\n    '.join(tail(output)), flush=True)
                    if passed: break
    finals = [result for result in results if result['final']]
    failed = [result for result in finals if result['status'] != 'PASS']
    print('\n' + summary_table(results, scales))
    passed = len(finals)-len(failed)
    print(f'\n{passed}/{len(finals)} passed by exit code'
          + (f'; {len(failed)} not passed: ' + ', '.join(f"{r['name']}@{r['scale']} {r['status']}" for r in failed)
             if failed else '') + f' ({(datetime.now()-started).total_seconds()/60:.1f} min)')
    destination = arguments.json or APP/'.user'/'smoke-runs'/f'{started:%Y%m%d-%H%M%S}.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(dict(
        started=started.isoformat(timespec='seconds'), python=sys.executable,
        python_version=platform.python_version(), platform=platform.platform(),
        headless=arguments.headless, scales=list(map(scale_text, scales)), timeout=arguments.timeout,
        repeat=arguments.repeat, retry=arguments.retry, passed=passed, total=len(finals),
        results=results), indent=1, ensure_ascii=False), encoding='utf-8')
    print(f'Summary: {destination}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
