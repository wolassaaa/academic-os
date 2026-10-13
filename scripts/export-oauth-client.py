"""Release-time export of the application desktop client, never user credentials."""
import argparse
import json
from pathlib import Path

from academic_os.security import SecretStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(SecretStore().get('google-oauth-client'))
    client = {name: raw[name] for name in ('client_id', 'client_secret')}
    if not all(isinstance(value, str) and value for value in client.values()) or not client['client_id'].endswith('.apps.googleusercontent.com'):
        raise ValueError('A valid application desktop OAuth client is required for release')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'installed': client}), encoding='utf-8')
    print('Application desktop client exported; no account credentials included.')


if __name__ == '__main__':
    main()
