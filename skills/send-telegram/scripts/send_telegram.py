#!/usr/bin/env python3
import sys
import os
import argparse
import subprocess
import json

def load_dotenv(filepath=".env"):
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))

def main():
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="Send message or file via Telegram.")
    parser.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN"), help="Telegram Bot Token (or set in .env)")
    parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"), dest="chat_id", help="Telegram Chat ID (or set in .env)")
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

    # Determine Telegram API Endpoint
    if args.file:
        # Use sendDocument for files
        endpoint = f"https://api.telegram.org/bot{args.token}/sendDocument"
    else:
        # Use sendMessage for simple chat
        endpoint = f"https://api.telegram.org/bot{args.token}/sendMessage"

    cmd = ["curl", "-s", "-X", "POST", endpoint, "-F", f"chat_id={args.chat_id}"]

    if args.message:
        if args.file:
            # If a file is sent, text is passed as a 'caption'
            cmd.extend(["-F", f"caption={args.message}"])
        else:
            # If strictly text, passed as 'text'
            cmd.extend(["-F", f"text={args.message}"])

    if args.file:
        # Add the file part
        cmd.extend(["-F", f"document=@{args.file}"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        resp = json.loads(result.stdout)
        
        if resp.get("ok"):
            print("Successfully sent to Telegram.")
            sys.exit(0)
        else:
            print(f"Telegram API Error: {resp.get('description', 'Unknown error')}", file=sys.stderr)
            sys.exit(1)
            
    except subprocess.CalledProcessError as e:
        print(f"Error executing curl: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Failed to parse Telegram response: {result.stdout}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
