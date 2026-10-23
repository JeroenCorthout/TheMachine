import json, struct, unittest
import assistant as a


def png_size(path):
    with open(path, 'rb') as f:
        head = f.read(24)
    return struct.unpack('>II', head[16:24])


class HomeScreenApp(unittest.TestCase):
    """Android installs the dashboard as a full-screen app from its manifest."""

    def test_manifest_is_installable_and_every_icon_is_served(self):
        manifest = json.loads((a.ROOT / 'manifest.webmanifest').read_text())
        self.assertEqual(manifest['display'], 'fullscreen')
        self.assertEqual((manifest['start_url'], manifest['scope']), ('.', '.'))  # relative: works under /assistant/
        purposes = set()
        for icon in manifest['icons']:
            path = a.BRAND_ASSETS['/' + icon['src']]
            self.assertEqual('%dx%d' % png_size(path), icon['sizes'], icon['src'])
            purposes.add((icon['sizes'], icon['purpose']))
        self.assertTrue({('192x192', 'any'), ('512x512', 'any'), ('512x512', 'maskable')} <= purposes)

    def test_own_origin_for_the_app_and_old_links_keep_working(self):
        config = {'dashboard_host': 'm.ts.net:8443', 'dashboard_aliases': ['m.ts.net']}
        self.assertEqual(a.dashboard_hosts(config), ['m.ts.net:8443', 'm.ts.net'])
        self.assertEqual(a.dashboard_hosts({'dashboard_host': 'm.ts.net'}), ['m.ts.net'])

    def test_dashboard_links_the_manifest(self):
        self.assertIn('<link rel="manifest" href="manifest.webmanifest" crossorigin="use-credentials">', (a.ROOT / 'dashboard.html').read_text())

    def test_something_wrong_goes_to_github_issues_or_the_hosted_support_form(self):
        page = (a.ROOT / 'dashboard.html').read_text()
        self.assertIn('id="report-problem" class="footer-link" href="https://github.com/Salesflare/TheMachine/issues/new"', page)
        self.assertIn("$('#report-problem').href=new URL('/support',state.manage_url).href", page)

    def test_footer_offers_the_source_of_the_running_version(self):
        # AGPL-3.0 section 13: people using the dashboard can get its source.
        page = (a.ROOT / 'dashboard.html').read_text()
        self.assertIn('id="source-code" class="footer-link" href="https://github.com/Salesflare/TheMachine"', page)
        self.assertIn("$('#source-code').href=state.source_url", page)

    def test_source_url_names_a_release_github_has_never_a_local_commit(self):
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as root:
            git = lambda *args: subprocess.run(['git', '-C', root, *args], check=True, capture_output=True)
            commit = lambda m: git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '--allow-empty', '-m', m)
            git('init', '-q')
            commit('x')
            a._SOURCE_URL.clear()
            self.assertEqual(a.source_url(root), a.SOURCE_REPO, 'a commit of its own: the repo, not a 404')
            git('tag', 'release-20261101-0900')
            a._SOURCE_URL.clear()
            self.assertEqual(a.source_url(root), a.SOURCE_REPO + '/tree/release-20261101-0900')
            commit('a local change on top')
            git('tag', 'my-own-tag')
            a._SOURCE_URL.clear()
            self.assertEqual(a.source_url(root), a.SOURCE_REPO + '/tree/release-20261101-0900', 'the latest release it contains')
        with tempfile.TemporaryDirectory() as root:  # an install that ports releases as its own commits
            git = lambda *args: subprocess.run(['git', '-C', root, *args], check=True, capture_output=True)
            commit = lambda m: git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '--allow-empty', '-m', m)
            git('init', '-q')
            commit('Promise pages (OSS #83, release-20261023-1930)')
            commit('A local fix')
            a._SOURCE_URL.clear()
            self.assertEqual(a.source_url(root), a.SOURCE_REPO + '/tree/release-20261023-1930', 'the release its last port names')
        a._SOURCE_URL.clear()
        self.assertEqual(a.source_url('/nonexistent-dir'), a.SOURCE_REPO)
        a._SOURCE_URL.clear()

    def test_source_url_right_after_boot(self):
        # time.monotonic() counts from boot: under 10 minutes of uptime an empty cache must not look fresh.
        from unittest import mock
        a._SOURCE_URL.clear()
        with mock.patch.object(a.time, 'monotonic', return_value=5.0):
            self.assertEqual(a.source_url('/nonexistent-dir'), a.SOURCE_REPO)
        a._SOURCE_URL.clear()


if __name__ == '__main__':
    unittest.main()
