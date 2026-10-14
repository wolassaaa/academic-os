"""Generate a JSON (also valid YAML) Hermes MCP fragment, without altering its profile."""
import argparse
import json
from pathlib import Path


def configuration(executable, config, token_ref='academic-os-hermes'):
    return {'mcp_servers': {'academic-os': {
        'command': str(Path(executable).resolve()),
        'args': ['--mcp', '--config', str(Path(config).resolve()), '--token-ref', token_ref],
        'timeout': 30,
        'tools': {'include': ['health', 'list_items', 'get_item', 'list_tags', 'create_tag', 'update_tag', 'set_item_tags',
                              'list_mail', 'get_mail', 'mail_digest', 'mail_feed', 'acknowledge_mail_feed', 'list_conflicts',
                              'list_drafts', 'generate_plan', 'create_draft', 'sync']}}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--token-ref', default='academic-os-hermes')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.executable.is_file():
        parser.error('Portable executable does not exist')
    if args.output.exists():
        parser.error('Choose a new output file; an existing Hermes profile is never overwritten')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(configuration(args.executable, args.config, args.token_ref), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Created Hermes MCP configuration fragment: ' + str(args.output.resolve()))


if __name__ == '__main__':
    main()
