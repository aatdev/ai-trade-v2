#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys

# Telegram hard limits: a sendMessage `text` may be up to 4096 chars, but a
# sendDocument/sendPhoto `caption` is capped at 1024. Long messages must
# therefore go out as standalone text, not as a file caption.
TG_TEXT_LIMIT = 4096
TG_CAPTION_LIMIT = 1024


def load_dotenv(filepath=".env"):
    if os.path.exists(filepath):
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export ") :].lstrip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def _split_text(text, limit=TG_TEXT_LIMIT):
    """Split text into <=limit-char chunks, preferring line boundaries and
    hard-splitting any single line that is itself longer than the limit."""
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # a single over-long line
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur += line
    if cur:
        chunks.append(cur)
    return chunks or [text]


def plan_requests(message, file, *, caption_limit=TG_CAPTION_LIMIT, text_limit=TG_TEXT_LIMIT):
    """Decide the ordered Telegram API calls for a message/file combination.

    - file + short message  -> one sendDocument with the message as caption
    - file + long message   -> sendMessage chunk(s) first, then the file alone
    - file only             -> sendDocument with no caption
    - message only          -> sendMessage chunk(s)
    """
    message = message or ""
    reqs = []
    if file:
        if message and len(message) <= caption_limit:
            reqs.append({"kind": "document", "file": file, "caption": message})
        else:
            if message:
                reqs += [{"kind": "message", "text": c} for c in _split_text(message, text_limit)]
            reqs.append({"kind": "document", "file": file, "caption": None})
    elif message:
        reqs += [{"kind": "message", "text": c} for c in _split_text(message, text_limit)]
    return reqs


def _build_curl(token, chat_id, req):
    # Use --form-string (not -F) for text fields: -F interprets a leading '@'/'<'
    # as a file reference and treats ';' as the start of a field modifier
    # (e.g. ';type='), silently truncating values that contain those. The
    # document part below does need -F so '@' loads the file.
    if req["kind"] == "message":
        endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
        cmd = ["curl", "-s", "-X", "POST", endpoint, "--form-string", f"chat_id={chat_id}"]
        cmd += ["--form-string", f"text={req['text']}"]
    else:  # document
        endpoint = f"https://api.telegram.org/bot{token}/sendDocument"
        cmd = ["curl", "-s", "-X", "POST", endpoint, "--form-string", f"chat_id={chat_id}"]
        if req.get("caption"):
            cmd += ["--form-string", f"caption={req['caption']}"]
        cmd += ["-F", f"document=@{req['file']}"]
    return cmd


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Send message or file via Telegram.")
    parser.add_argument(
        "--token",
        default=os.environ.get("TELEGRAM_BOT_TOKEN"),
        help="Telegram Bot Token (or set in .env)",
    )
    parser.add_argument(
        "--chat-id",
        default=os.environ.get("TELEGRAM_CHAT_ID"),
        dest="chat_id",
        help="Telegram Chat ID (or set in .env)",
    )
    parser.add_argument("--message", help="Text message or caption (if sending file)")
    parser.add_argument("--file", help="Path to file to send")

    args = parser.parse_args()

    if not args.token or not args.chat_id:
        print("Error: --token and --chat-id are required (via args or .env).", file=sys.stderr)
        sys.exit(1)

    if not args.message and not args.file:
        print("Error: Must provide at least --message or --file.", file=sys.stderr)
        sys.exit(1)

    if args.file and not os.path.exists(args.file):
        print(f"Error: File not found ({args.file})", file=sys.stderr)
        sys.exit(1)

    for req in plan_requests(args.message, args.file):
        cmd = _build_curl(args.token, args.chat_id, req)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            resp = json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"Error executing curl: {e}", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError:
            print(f"Failed to parse Telegram response: {result.stdout}", file=sys.stderr)
            sys.exit(1)

        if not resp.get("ok"):
            print(
                f"Telegram API Error: {resp.get('description', 'Unknown error')}", file=sys.stderr
            )
            sys.exit(1)

    print("Successfully sent to Telegram.")
    sys.exit(0)


if __name__ == "__main__":
    main()
