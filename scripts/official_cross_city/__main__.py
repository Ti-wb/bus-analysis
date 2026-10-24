"""python -m scripts.official_cross_city {ntpc,population,metro-od,ntpc-pathdetail} --help"""
import sys
from . import ntpc, population, metro_od, ntpc_pathdetail


def main():
    commands = {"ntpc": ntpc.main, "population": population.main, "metro-od": metro_od.main, "ntpc-pathdetail": ntpc_pathdetail.main}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(__doc__)
        return 0 if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"} else 2
    try:
        commands[sys.argv[1]](sys.argv[2:])
        return 0
    except (OSError, ValueError, KeyError) as exc:
        # No successful output or zero-valued placeholder is published after a failure.
        print(f"Official-source collection failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
