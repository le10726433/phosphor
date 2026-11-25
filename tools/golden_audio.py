"""Explicitly generate or check the versioned audio safety net; never plays audio."""
import _path  # noqa: F401
import argparse
from pathlib import Path

import numpy as np

from tests.unit.golden_scenes import SCENES, WINDOW_STARTS, RATE, snapshot, assert_snapshot

GOLDEN = Path(__file__).resolve().parents[1] / 'tests/unit/golden_audio.npz'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--update', action='store_true')
    mode.add_argument('--check', action='store_true')
    parser.add_argument('--scene', choices=SCENES, help='Update/check only this scene, preserving all other reference arrays.')
    args = parser.parse_args()
    if args.update:
        print('WARNING: updating golden audio is only for intentional audio changes.')
        if args.scene and GOLDEN.exists():
            with np.load(GOLDEN, allow_pickle=False) as existing:
                arrays = {key: existing[key].copy() for key in existing.files}
        else:
            arrays = {'window_starts': np.array(WINDOW_STARTS), 'sample_rate': np.array(RATE)}
        for name in ((args.scene,) if args.scene else SCENES):
            arrays.update({f'{name}__{key}': value for key, value in snapshot(name).items()})
        np.savez_compressed(GOLDEN, **arrays)
        # Raised from 1.5 MB at 0.61.0 (14 scenes, 1.55 MB) and from 2 MB at 0.67.0 (19 scenes, 2.13 MB).
        if GOLDEN.stat().st_size >= 3_000_000:
            raise RuntimeError('Golden archive exceeds 3 MB')
        print(f'Wrote {GOLDEN} ({GOLDEN.stat().st_size} bytes)')
        return 0
    if not GOLDEN.exists():
        parser.error('Golden archive is missing; generate the initial baseline with --update.')
    failed = False
    with np.load(GOLDEN, allow_pickle=False) as golden:
        for name in ((args.scene,) if args.scene else SCENES):
            actual = snapshot(name)
            difference = max(float(np.max(np.abs(value - golden[f'{name}__{key}'])))
                             for key, value in actual.items())
            print(f'{name:20s} max abs difference {difference:.12g}')
            try:
                assert_snapshot(name, actual, golden)
            except AssertionError as error:
                failed = True
                print(error)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
