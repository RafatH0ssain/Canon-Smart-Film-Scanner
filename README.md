# Canon Smart Film Scanner

[![tests](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner/actions/workflows/tests.yml/badge.svg)](https://github.com/RafatH0ssain/Canon-Smart-Film-Scanner/actions/workflows/tests.yml)

I scan my film negatives with a Canon R7 on a copy stand. This controls the camera over Wi-Fi so I
can focus and shoot from my laptop, with the live view already inverted to a positive.

Wi-Fi was too slow on my camera (about 4 fps), so I mostly use the
[USB version](https://github.com/RafatH0ssain/Canon-EDSDK-Film-Scanner) now. After scanning, I use
[Film Scan Darkroom](https://github.com/RafatH0ssain/Film-Scan-Darkroom) to crop and invert the roll.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -e .
python -m csfs.mock.mock_server   # fake camera, if you don't have one connected
python -m csfs.app.server
```

Then open http://127.0.0.1:8000. For a real camera you need to enable Canon's Camera Control API
through their [developer site](https://developercommunity.usa.canon.com/), then put the camera's
address in `config.yaml`.

## Licence

MIT.
