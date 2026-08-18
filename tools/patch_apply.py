"""Exact-match, uniqueness-checked in-place patcher.

Convention 21: this working directory is shared and other sessions edit the
same files. A full-file rewrite would silently clobber whatever another session
wrote between our read and our write. This applies a targeted replacement and
refuses unless the anchor appears EXACTLY once, so a file that moved under us
fails loudly instead of losing someone's work.

    env -u PYTHONPATH python3 tools/patch_apply.py <patchfile.json>

The patch file is a JSON list of {file, old, new} objects. Every replacement is
verified before ANY file is written: a patch set is all-or-nothing, because a
half-applied set leaves the tree in a state nobody designed.
"""
import json
import sys


def main(argv):
    if len(argv) != 2:
        print('usage: patch_apply.py <patchfile.json>', file=sys.stderr)
        return 2

    with open(argv[1]) as fh:
        patches = json.load(fh)

    # Pass 1: verify everything, write nothing.
    staged = {}
    for i, p in enumerate(patches):
        path, old, new = p['file'], p['old'], p['new']
        text = staged.get(path)
        if text is None:
            with open(path) as fh:
                text = fh.read()
        n = text.count(old)
        if n != 1:
            print('PATCH {} REFUSED: anchor appears {} times in {} '
                  '(need exactly 1)'.format(i, n, path), file=sys.stderr)
            print('anchor was:\n{}'.format(old[:400]), file=sys.stderr)
            return 1
        staged[path] = text.replace(old, new, 1)

    # Pass 2: commit.
    for path, text in staged.items():
        with open(path, 'w') as fh:
            fh.write(text)
        print('patched {}'.format(path))
    print('{} replacement(s) across {} file(s)'.format(len(patches), len(staged)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
