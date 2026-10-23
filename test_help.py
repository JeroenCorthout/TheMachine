"""The help articles (docs/help/, shown at themachine.computer/help) stay in line with the code: scripts/help_check.py."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('help_check', HERE / 'scripts' / 'help_check.py')
help_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(help_check)

GOOD = '''---
title: Gmail
area: connections
summary: Reads your mail.
covers: connector:gmail
code: onboarding.py
---
Open **Connections**. See [Slack](slack.md) and [Security](/security).
'''


class HelpArticles(unittest.TestCase):
    def test_every_feature_has_an_article_and_every_article_matches_the_code(self):
        self.assertEqual(help_check.check(), [])

    def write(self, folder, name, text):
        (Path(folder) / name).write_text(text, encoding='utf-8')

    def problems(self, text, extra=()):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, 'gmail.md', text)
            self.write(d, 'slack.md', GOOD.replace('connector:gmail', 'connector:slack'))
            for name, body in extra:
                self.write(d, name, body)
            return [p for p in help_check.check(Path(d)) if p.startswith('docs/help/gmail.md')]

    def test_links_go_to_help_articles_never_to_docs_on_github(self):
        html = (HERE / 'dashboard.html').read_text(encoding='utf-8')
        self.assertNotRegex(html, r'github\.com/Salesflare/TheMachine/(blob|tree)/[^/]+/docs/')
        self.assertIn('href="https://themachine.computer/help/work-hours"', html)
        found = help_check.articles()
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / 'page.html'
            page.write_text('<a href="https://github.com/Salesflare/TheMachine/blob/main/docs/usage.md#work-hours">Learn more</a>'
                            '<a href="https://themachine.computer/help/no-such-article">x</a>'
                            '<a href="https://themachine.computer/help/work-hours#no-such-heading">x</a>'
                            '<a href="${HELP}friends#find-people-you-know">x</a>', encoding='utf-8')
            problems = help_check.check_links(found, [page])
        self.assertEqual(len(problems), 3, problems)
        self.assertIn('on GitHub', problems[0])
        self.assertTrue(any('no-such-article' in p for p in problems) and any('no-such-heading' in p for p in problems))

    def test_a_good_article_passes(self):
        self.assertEqual(self.problems(GOOD), [])

    def test_text_the_app_no_longer_shows_fails(self):
        self.assertIn('names "Connect it now"', ' '.join(self.problems(GOOD.replace('**Connections**', '**Connect it now**'))))
        self.assertIn('route /no-such-page', ' '.join(self.problems(GOOD + 'Go to `/no-such-page`.\n')))
        self.assertIn('`assistant teleport`', ' '.join(self.problems(GOOD + 'Run `assistant teleport`.\n')))
        self.assertEqual(self.problems(GOOD.replace('code: onboarding.py', 'code: onboarding.py\nsite: Connect it now')
                                       .replace('**Connections**', '**Connect it now**')), [])  # hosted pages' text

    def test_a_feature_or_path_that_is_gone_fails(self):
        self.assertIn('connector:fax', ' '.join(self.problems(GOOD.replace('connector:gmail', 'connector:fax'))))
        self.assertIn('gone.py does not exist', ' '.join(self.problems(GOOD.replace('onboarding.py', 'gone.py'))))

    def test_a_feature_without_an_article_fails(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, 'gmail.md', GOOD)
            problems = help_check.check(Path(d))
        self.assertIn('connector:slack has no help article: add one in docs/help/ with "covers: connector:slack" (or a reason in PENDING)', problems)
        self.assertNotIn('connector:gmail has no help article', ' '.join(problems))

    def test_house_style(self):
        for text, want in ((GOOD + 'Fast — and calm.\n', 'em dash'), (GOOD + 'You can open it.\n', 'starts with "You"'),
                           (GOOD + '- You can open it.\n', 'starts with "You"'), (GOOD + 'a' * 430 + '\n', 'over 420'),
                           (GOOD + '| a | b |\n', 'does not render'), (GOOD + '# Big\n', 'does not render'),
                           (GOOD + '[x](nowhere.md)\n', 'not an article'), (GOOD + 'From {price.cheap}.\n', 'unknown placeholder'), (GOOD + '[x](/nowhere)\n', 'not a page of the site'),
                           (GOOD.replace('area: connections', 'area: misc'), 'area')):
            self.assertIn(want, ' '.join(self.problems(text)), text[-40:])


if __name__ == '__main__':
    unittest.main()
