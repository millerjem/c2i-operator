#!/usr/bin/env python3
"""Generate an embeddable card JSON document from a request and optional profile JSON."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from c2i import make_card

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('request', type=Path)
parser.add_argument('--profile', type=Path, help='VerteX profile JSON, needed to generate settings fields')
parser.add_argument('--output', type=Path)
args = parser.parse_args()
request = json.loads(args.request.read_text())
profile = json.loads(args.profile.read_text()) if args.profile else None
card = make_card(request, request.get('status', {}), profile)
result = json.dumps(card, indent=2) + '\n'
if args.output:
    args.output.write_text(result)
else:
    print(result, end='')
