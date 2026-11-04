"""Library heads, immutable history, cleanup recoverability and protected baselines."""
from pathlib import Path
import json
import tempfile
import unittest
from projects import ProjectStore, RevisionStore

A = [{'kind':'wait','name':'one','duration_s':.2}]
B = [{'kind':'wait','name':'two','duration_s':.4}]


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(self.temp.name)
    def tearDown(self): self.temp.cleanup()

    def test_edit_updates_single_head_and_keeps_old_content(self):
        a = self.store.save('my recipe', A)
        b = self.store.save('my recipe', B, a['project_id'], a['revision'])
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(a['project_id'], b['project_id'])
        self.assertEqual(b['version_number'], 2)
        self.assertEqual(self.store.get(a['revision'])['actions'], A)
        self.assertEqual(len(self.store.history(a['project_id'])), 2)
        self.assertEqual(ProjectStore(self.temp.name).list()[0]['revision'], b['revision'])

    def test_stale_save_does_not_replace_newer_head(self):
        a = self.store.save('recipe', A)
        b = self.store.save('recipe', B, a['project_id'], a['revision'])
        with self.assertRaisesRegex(ValueError, 'PROJECT_VERSION_CONFLICT'):
            self.store.save('recipe', A, a['project_id'], a['revision'])
        self.assertEqual(self.store.list()[0]['revision'], b['revision'])

    def test_builtin_is_protected_but_can_be_forked(self):
        a = self.store.save('standard', A, builtin_key='standard')
        for call in [lambda:self.store.archive(a['project_id']),
                     lambda:self.store.save('bad', B, a['project_id'],a['revision']),
                     lambda:self.store.restore(a['project_id'],a['revision'],a['revision'])]:
            with self.assertRaisesRegex(ValueError,'BUILTIN_READ_ONLY'):call()
        fork = self.store.save('my variant', B)
        self.assertEqual(len(self.store.list()), 2)
        self.assertNotEqual(a['project_id'],fork['project_id'])
        self.assertEqual(self.store.get(a['revision'])['actions'], A)

    def test_cleanup_backs_up_and_archives_without_deleting_revisions(self):
        a = self.store.save('draft', A)
        self.store.save('standard', B, builtin_key='standard')
        result = self.store.archive_custom()
        self.assertEqual(result['archived_projects'], 1)
        self.assertTrue(Path(result['backup_file']).exists())
        backup = json.loads(Path(result['backup_file']).read_text())
        self.assertEqual(len(backup['records']), 2)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.store.list(True)), 2)
        self.assertEqual(self.store.get(a['revision'])['actions'], A)
        self.store.archive(a['project_id'], False)
        self.assertEqual(len(self.store.list()), 2)

    def test_restore_history_changes_head_without_new_version(self):
        a = self.store.save('recipe', A)
        b = self.store.save('renamed', B, a['project_id'], a['revision'])
        restored = self.store.restore(a['project_id'],a['revision'],b['revision'])
        self.assertEqual(restored['actions'], A)
        self.assertEqual(restored['version_count'], 2)
        self.assertEqual(self.store.list()[0]['revision'],a['revision'])
        self.assertEqual(self.store.get(b['revision'])['actions'], B)

    def test_legacy_same_name_versions_become_one_project(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = RevisionStore(directory)
            # RevisionStore.get is raw here, no library index yet.
            a, b = legacy.save('same', A), legacy.save('same', B)
            current = ProjectStore(directory)
            self.assertEqual(len(current.list()),1)
            self.assertEqual(current.list()[0]['version_count'],2)
            self.assertEqual(current.get(a['revision'])['actions'],A)
            self.assertEqual(current.get(b['revision'])['actions'],B)

    def test_run_outcomes_are_per_revision_not_inherited_by_edits(self):
        a = self.store.save('recipe', A)
        self.store.note_result(a['revision'],'job1','succeeded')
        self.assertEqual(self.store.list()[0]['validation_status'],'succeeded')
        b = self.store.save('recipe',B,a['project_id'],a['revision'])
        self.assertEqual(self.store.list()[0]['validation_status'],'unverified')
        self.assertEqual(self.store.get(a['revision'])['validation_status'],'succeeded')

    def test_archived_names_and_shared_content_keep_project_identity(self):
        a = self.store.save('recipe', A)
        self.store.archive(a['project_id'])
        b = self.store.save('recipe', A)
        self.assertNotEqual(a['name'], b['name'])
        self.store.archive(a['project_id'], False)
        renamed = self.store.save('renamed', B, a['project_id'], a['revision'])
        # Another project can reuse an old historical name/content; history
        # queries must retain the explicitly selected owning project.
        other = self.store.save('recipe', A)
        self.assertEqual(other['revision'], a['revision'])
        historical = self.store.get(a['revision'], a['project_id'])
        self.assertEqual(historical['project_id'], a['project_id'])
        self.assertEqual(historical['head_revision'], renamed['revision'])
        with self.assertRaisesRegex(ValueError, 'PROJECT_NAME_EXISTS'):
            self.store.restore(a['project_id'], a['revision'], renamed['revision'])

    def test_explicit_save_as_has_distinct_name_and_identity(self):
        a = self.store.save('recipe',A)
        b = self.store.save('recipe',A,new_project=True)
        self.assertNotEqual(a['project_id'],b['project_id'])
        self.assertNotEqual(a['name'],b['name'])


if __name__ == '__main__': unittest.main()
