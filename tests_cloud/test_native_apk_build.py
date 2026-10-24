"""Static offline-loader contract checks, never native game execution."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from tools.cloud import build_native_apk as builder


class NativeAPKBuildTests(unittest.TestCase):
    def setUp(self):
        base = builder.local('.tmp/native-apk-tests')
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.game = self.folder / 'com/supercell/clashroyale/GameApp.smali'
        self.titan = self.folder / 'com/supercell/titan/GameApp.smali'
        self.game.parent.mkdir(parents=True)
        self.titan.parent.mkdir(parents=True)
        self.game.write_text('''.method public final a()Z
    .locals 3
    const-string v0, "crprobe"
    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V
    const/4 v0, 0x0
    return v0
    invoke-static {}, Lfixture/OnlineLoader;->originalUnreachablePath()V
.end method
''')
        self.titan.write_text('''.method private static native nativeBootstrapProbe()V
.end method
    const-string v3, "scid_sdk"
    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V
    const-string v3, "g"
    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V
    const-string v3, "crprobe"
    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V
    invoke-static {}, Lcom/supercell/titan/GameApp;->nativeBootstrapProbe()V
''')

    def test_offline_gate_and_post_libg_order_contract(self):
        checks = builder.verify_patch_tree(self.folder)
        self.assertTrue(all(checks.values()))

    def test_returning_true_or_calling_online_loader_first_fails(self):
        original = self.game.read_text()
        variants = [original.replace('const/4 v0, 0x0', 'const/4 v0, 0x1'),
                    original.replace('    const-string v0',
                        '    invoke-static {}, Lfixture/OnlineLoader;->earlyCall()V\n    const-string v0', 1)]
        for text in variants:
            self.game.write_text(text)
            with self.assertRaisesRegex(ValueError, 'immediately return false'):
                builder.verify_patch_tree(self.folder)

    def test_bootstrap_before_game_library_is_rejected(self):
        text = self.titan.read_text()
        text = text.replace('const-string v3, "g"', 'const-string v3, "crprobe"', 1)
        # Merely containing every marker is insufficient; order matters.
        text += '\n    const-string v3, "g"\n'
        self.titan.write_text(text)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            builder.verify_patch_tree(self.folder)

    def test_missing_native_bootstrap_declaration_is_rejected(self):
        self.titan.write_text(self.titan.read_text().replace(
            '.method private static native nativeBootstrapProbe()V', '.method private static unused()V'))
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            builder.verify_patch_tree(self.folder)

    def test_tool_budget_prevents_all_downloads(self):
        with patch.object(builder, 'fetch_resource') as fetch:
            with self.assertRaisesRegex(ValueError, 'budget'):
                builder.prepare_tools(1)
        fetch.assert_not_called()

    def test_existing_build_evidence_is_preserved(self):
        marker = self.folder / 'report.json'
        marker.write_text('previous evidence')
        with self.assertRaisesRegex(ValueError, 'preserved'):
            builder.build(str(self.folder))
        self.assertEqual(marker.read_text(), 'previous evidence')

    def test_direct_script_help_runs_in_clean_subprocess(self):
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, str(builder.local('tools/cloud/build_native_apk.py')), '--help'],
                                cwd=builder.ROOT, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--verify-existing', result.stdout)

    def test_small_decoding_input_keeps_exact_signed_dex_bytes(self):
        apk = self.folder / 'signed-byte-fixture.apk'
        dex = self.folder / 'extracted.dex'
        dex.write_bytes(b'fixture code bytes; no game execution')
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr('classes.dex', dex.read_bytes())
            archive.writestr('AndroidManifest.xml', b'fixture manifest bytes')
            archive.writestr('resources.arsc', b'fixture resource table bytes')
            archive.writestr('assets/large-content-not-needed', b'fixture asset')
        proof = builder.code_proof_archive(apk, self.folder)
        with zipfile.ZipFile(proof) as archive:
            self.assertNotIn('assets/large-content-not-needed', archive.namelist())
        self.assertTrue(builder.verify_code_proof(apk, proof, dex)['all_three_dex_hashes_exact'])
        dex.write_bytes(b'different code')
        self.assertFalse(builder.verify_code_proof(apk, proof, dex)['all_three_dex_hashes_exact'])


if __name__ == '__main__':
    unittest.main()
