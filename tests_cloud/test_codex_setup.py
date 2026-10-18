"""Protect existing project instructions/settings when adding the remote extension."""
import json
from pathlib import Path
import tempfile
import unittest
from configure_codex import configure,path_in,BEGIN

class CodexSetup(unittest.TestCase):
    def fixture(self):
        cache=Path(__file__).resolve().parents[1]/'.tmp/cloud';cache.mkdir(parents=True,exist_ok=True)
        tmp=tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        (root/'tools/cloud').mkdir(parents=True);(root/'tools/cloud/workspace.py').write_text('# fixture',encoding='utf8')
        (root/'.devcontainer').mkdir();(root/'.devcontainer/devcontainer.json').write_text(json.dumps({'image':'keep-image','customizations':{'vscode':{'extensions':['keep-extension'],'settings':{'keep':True}}}}),encoding='utf8')
        (root/'AGENTS.md').write_text('Friends original guidance\n',encoding='utf8');(root/'.gitignore').write_text('friend-cache/\n',encoding='utf8')
        return root
    def test_preview_does_not_write(self):
        root=self.fixture();before=(root/'AGENTS.md').read_bytes();r=configure(root)
        self.assertEqual((root/'AGENTS.md').read_bytes(),before);self.assertFalse((root/'.tmp').exists());self.assertIn('AGENTS.md',r['changed_files'])
    def test_merge_backup_and_idempotence(self):
        root=self.fixture();r=configure(root,True);cfg=json.loads((root/'.devcontainer/devcontainer.json').read_text(encoding='utf8'))
        self.assertEqual(cfg['image'],'keep-image');self.assertEqual(cfg['customizations']['vscode']['extensions'],['keep-extension','openai.chatgpt']);self.assertTrue(cfg['customizations']['vscode']['settings']['keep'])
        self.assertTrue((root/'AGENTS.md').read_text(encoding='utf8').startswith('Friends original guidance'))
        self.assertEqual((Path(r['backup'])/'AGENTS.md').read_text(encoding='utf8'),'Friends original guidance\n')
        self.assertTrue((root/'.gitignore').read_text(encoding='utf8').startswith('friend-cache/'))
        self.assertEqual(configure(root,True)['changed_files'],[])
    def test_invalid_configuration_preserves_all_files(self):
        root=self.fixture();(root/'.devcontainer/devcontainer.json').write_text('invalid json',encoding='utf8');before=(root/'AGENTS.md').read_bytes()
        with self.assertRaises(ValueError):configure(root,True)
        self.assertEqual((root/'AGENTS.md').read_bytes(),before);self.assertFalse((root/'.tmp').exists())
    def test_path_cannot_escape(self):
        root=self.fixture()
        with self.assertRaises(ValueError):path_in(root,'../escape')
    def test_incomplete_managed_block_is_preserved(self):
        root=self.fixture();(root/'AGENTS.md').write_text('Friends content\n'+BEGIN,encoding='utf8')
        with self.assertRaises(ValueError):configure(root,True)
        self.assertFalse((root/'.tmp').exists())
