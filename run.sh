#!/bin/bash
# Convenience wrapper — uses the Homebrew Python 3.14 to run lovelace.
# Usage: ./run.sh generate [options]
#        ./run.sh inspect <file.zoia>
#        ./run.sh roundtrip <file.zoia>
exec /opt/homebrew/opt/python@3.14/bin/python3.14 -m lovelace.cli "$@"
