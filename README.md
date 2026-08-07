# Briar Hermes Plugin

Experimental Hermes Agent gateway adapter for Briar.

This is a minimal scaffold, not a working integration yet. It assumes a separate
Briar API bridge exposing HTTP endpoints like:

- `GET /status`
- `GET /messages?contact_id=<id>`
- `POST /send` with JSON `{contact_id, message, reply_to?}`

## Install

```bash
git clone git@github.com:<your-org>/briar-hermes-plugin.git ~/code/briar
ln -sfn ~/code/briar ~/.hermes/plugins/briar
```

## Configure

```bash
export BRIAR_API_URL="http://127.0.0.1:7000"
export BRIAR_CONTACT_ID="<contact-id>"
export BRIAR_HOME_CHANNEL="<contact-id>"
export BRIAR_ALLOWED_USERS="<contact-id>,<other-id>"
```

## Next steps

- Implement or integrate an actual Briar local transport bridge
- Replace stub `/status`, `/messages`, and `/send` with real transport
- Add tests matching Hermes' gateway adapter conventions
