"""Exercise archive trust boundaries and native ELF identity without execution."""
import importlib.util
from pathlib import Path
import stat
import struct
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('native_client', Path(__file__).with_name('native-client.py'))
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class NativeClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def archive(self, entries):
        path = self.base / 'client.zip'
        with zipfile.ZipFile(path, 'w') as bundle:
            for name, content, mode in entries:
                item = zipfile.ZipInfo(name)
                item.create_system = 3
                item.external_attr = mode << 16
                bundle.writestr(item, content)
        return path

    def test_hash_mismatch(self):
        source = self.base / 'source'
        source.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            client.fetch(source.as_uri(), '0' * 64, self.base / 'cache')
        self.assertFalse((self.base / 'cache').exists())

    def test_cache_mismatch(self):
        cache = self.base / 'cache'
        cache.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'cache hash'):
            client.fetch('https://invalid.example', '0' * 64, cache)

    def test_traversal(self):
        for name in ('../escape', '/escape', 'a\\..\\escape', 'C:\\escape'):
            with self.subTest(name=name):
                path = self.archive([(name, b'bad', 0o644)])
                with self.assertRaises(ValueError):
                    client.extract(path, self.base / 'seed')
        self.assertFalse((self.base / 'escape').exists())

    def test_backslash_and_elf_mode(self):
        archive = self.archive([('steamrtarm64\\steam', b'\x7fELFdata', 0o644)])
        client.extract(archive, self.base / 'seed')
        output = self.base / 'seed/steamrtarm64/steam'
        self.assertEqual(output.read_bytes(), b'\x7fELFdata')
        self.assertEqual(output.stat().st_mode & 0o777, 0o755)

    def test_safe_symlink(self):
        archive = self.archive([('lib/target', b'ok', 0o644), ('lib/link', b'target', stat.S_IFLNK | 0o777)])
        client.extract(archive, self.base / 'seed')
        self.assertTrue((self.base / 'seed/lib/link').is_symlink())
        self.assertEqual((self.base / 'seed/lib/link').read_bytes(), b'ok')

    def test_relative_symlink(self):
        archive = self.archive([('lib/target', b'ok', 0o644), ('sub/link', b'../lib/target', stat.S_IFLNK | 0o777)])
        client.extract(archive, self.base / 'seed')
        self.assertEqual((self.base / 'seed/sub/link').read_bytes(), b'ok')

    def test_symlink_escape(self):
        for link in ('../../escape', '/tmp/escape'):
            archive = self.archive([('sub/link', link.encode(), stat.S_IFLNK | 0o777)])
            with self.assertRaises(ValueError):
                client.extract(archive, self.base / 'seed')

    def test_no_write_through_symlink(self):
        archive = self.archive([('target', b'ok', 0o644), ('link', b'target', stat.S_IFLNK | 0o777), ('link', b'changed', 0o644)])
        with self.assertRaisesRegex(ValueError, 'through symlink'):
            client.extract(archive, self.base / 'seed')
        self.assertEqual((self.base / 'seed/target').read_bytes(), b'ok')

    def test_aarch64_identity(self):
        root = self.base / 'seed/steamrtarm64'
        root.mkdir(parents=True)
        header = bytearray(20)
        header[:6] = b'\x7fELF\x02\x01'
        header[18:20] = struct.pack('<H', 183)
        for name in ('steam', 'steamwebhelper', 'steamclient.so'):
            (root / name).write_bytes(header)
        self.assertEqual(len(client.verify_arm64(root.parent)), 3)
        header[18:20] = struct.pack('<H', 62)
        (root / 'steam').write_bytes(header)
        with self.assertRaisesRegex(ValueError, 'AArch64'):
            client.verify_arm64(root.parent)


if __name__ == '__main__':
    unittest.main()
