# shortloop
🔗 Long URLs are a bad time. We fixed that.

## Run it
`pip install -r requirements.txt && python seed.py && python app.py`, then open http://localhost:8000 and log in as demo@example.com / demo-password-123.

## Honest disclaimer
This is only a test app and has never been run, so expect a typo or two. Local use only: scan the QR codes before trusting them, and never deploy this.

## Things that went wrong while building this
- `/{slug}` happily swallowed `/{slug}+`, so the preview route has to be registered first.
- Strict CSP would have eaten my inline-styled bar charts, so they're `<progress>` elements now.

Docs live in `docs/`. Now go click something.
