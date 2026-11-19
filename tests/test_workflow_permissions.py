"""Permission boundaries for the update workflow, without a YAML dependency.

Actions are pinned to full commit SHAs (JP-06); the version lives only in
the trailing `# vX.Y.Z` comment so Dependabot can update it. These tests
therefore match a 40-hex SHA, never a moving tag such as `@v4`.
"""
from pathlib import Path
import re
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/update.yml'

SHA_REF = r'[0-9a-f]{40}'


def block(text, name, indent):
    """Read an indentation-delimited mapping entry in this workflow."""
    match = re.search(rf'^{" " * indent}{re.escape(name)}:\s*\n', text, re.M)
    if match is None:
        raise AssertionError(f'Missing mapping: {name}')
    tail = text[match.end():]
    end = re.search(rf'^ {{0,{indent}}}\S', tail, re.M)
    return tail[:end.start()] if end else tail


def permissions(text, indent):
    body = block(text, 'permissions', indent)
    return dict(re.findall(r'^\s+([\w-]+):\s*(read|write|none)\s*$', body, re.M))


class WorkflowPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.jobs = block(self.text, 'jobs', 0)

    def test_top_level_permissions(self):
        self.assertEqual(permissions(self.text, 0), {'contents': 'read'})

    def test_update_has_no_write_or_checkout_credentials(self):
        update = block(self.jobs, 'update', 2)
        self.assertEqual(permissions(update, 4), {'contents': 'read'})
        checkout = update.split('uses: actions/checkout@', 1)[1].split('\n      -', 1)[0]
        self.assertIn('persist-credentials: false', checkout)
        self.assertNotRegex(update, r'\bgit\s+(?:commit|push)\b')

    def test_only_commit_and_deploy_can_write(self):
        expected = {
            'update': {'contents': 'read'},
            'commit': {'contents': 'write'},
            'deploy': {'pages': 'write', 'id-token': 'write'},
        }
        names = re.findall(r'^  ([\w-]+):\s*$', self.jobs, re.M)
        self.assertEqual(set(names), set(expected))
        for name in names:
            self.assertEqual(permissions(block(self.jobs, name, 2), 4), expected[name])

    def test_commit_runs_no_dependencies_or_collection_code(self):
        commit = block(self.jobs, 'commit', 2)
        self.assertNotRegex(commit, r'(?i)\b(?:pip\d*|npm|python[\d.]*)\b')
        uses = re.findall(r'uses: (\S+)', commit)
        self.assertEqual(len(uses), 2)
        self.assertRegex(uses[0], rf'^actions/checkout@{SHA_REF}$')
        self.assertRegex(uses[1], rf'^actions/download-artifact@{SHA_REF}$')
        # Moving tags must not come back: the version lives only in the
        # trailing comment, the reference itself is a full SHA.
        self.assertNotRegex(commit, r'uses: \S+@v\d')
        self.assertIn('needs: update', commit)
        self.assertIn('git diff --cached --quiet', commit)
        self.assertIn('git pull --rebase origin main', commit)
        self.assertIn('git push', commit)

    def test_artifact_handoff_and_deployment_order(self):
        update = block(self.jobs, 'update', 2)
        commit = block(self.jobs, 'commit', 2)
        deploy = block(self.jobs, 'deploy', 2)
        self.assertRegex(update, rf'actions/upload-artifact@{SHA_REF} # v[\d.]+')
        artifact = 'name: site-data-${{ github.run_id }}-${{ github.run_attempt }}'
        self.assertIn(artifact, update)
        self.assertIn(artifact, commit)
        self.assertIn('            docs\n            state/filter_audit.json\n            state/3hentai_thumb_attempts.json', update)
        self.assertRegex(update, rf'actions/upload-pages-artifact@{SHA_REF} # v[\d.]+')
        self.assertIn('needs: [update, commit]', deploy)
