"""One-time script to convert trivia2.pkl (Python 2 pickle) to trivia2.json.

Run this once:  python convert_trivia_pkl_to_json.py

Tries several strategies to load the old pickle file, then writes JSON.
"""
import pickle, json, os

pkl_path = os.path.join(os.path.dirname(__file__), 'trivia2.pkl')
json_path = os.path.join(os.path.dirname(__file__), 'trivia2.json')

# Strategy 1: fix text-mode line endings, then load with latin1
with open(pkl_path, 'rb') as f:
    raw = f.read()

for encoding in ('latin1', 'bytes', 'utf-8'):
    for fix_endings in (True, False):
        try:
            data = raw.replace(b'\r\n', b'\n') if fix_endings else raw
            trivia = pickle.loads(data, encoding=encoding)
            # If encoding='bytes', keys/values may be bytes — decode them
            if encoding == 'bytes':
                def decode(obj):
                    if isinstance(obj, bytes):
                        return obj.decode('latin1')
                    if isinstance(obj, dict):
                        return {decode(k): decode(v) for k, v in obj.items()}
                    if isinstance(obj, (list, tuple)):
                        return [decode(x) for x in obj]
                    return obj
                trivia = decode(trivia)
            print("Loaded with encoding=%r, fix_endings=%s" % (encoding, fix_endings))
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(trivia, f, ensure_ascii=False, indent=2)
            print("Wrote %s" % json_path)
            raise SystemExit(0)
        except (pickle.UnpicklingError, Exception) as e:
            continue

print("ERROR: Could not load %s with any strategy." % pkl_path)
print("You may need to find a Python 2 installation to convert it.")
raise SystemExit(1)
