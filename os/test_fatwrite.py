"""fatwrite.py against real tools: mkfs.fat makes the volume, fsck.fat and 7z judge the result."""
import hashlib, os, shutil, subprocess, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
import sys; sys.path.insert(0, str(HERE))
import fatwrite


@unittest.skipUnless(shutil.which('mkfs.fat') and shutil.which('fsck.fat') and shutil.which('7z'),
                     'needs mkfs.fat, fsck.fat and 7z on the host (not installed here => NOT verified)')
class FatWriteTests(unittest.TestCase):
    def setUp(self):
        if not os.environ.get('TMPDIR', '').startswith('/mnt/hdd/'):
            self.skipTest('set TMPDIR to a directory on the HDD (images never go to /tmp): NOT run')

    def build(self, tree, size_mb=64, offset=1 << 20, cluster_sectors=None):
        work = Path(tempfile.mkdtemp(prefix='wispos-fat-'))
        image = work / 'disk.img'
        with open(image, 'wb') as f:
            f.truncate(offset + size_mb * (1 << 20))
        cmd = ['mkfs.fat', '-F', '32', '-n', 'WISPTEST', f'--offset={offset // 512}']
        if cluster_sectors:
            cmd += ['-s', str(cluster_sectors)]
        subprocess.run(cmd + [str(image), str(size_mb * 1024)], check=True, capture_output=True)
        fatwrite.populate(image, offset, tree)
        return work, image, offset

    def sample_tree(self):
        tree = Path(tempfile.mkdtemp(prefix='wispos-fat-src-'))
        (tree / 'KERNEL').write_bytes(os.urandom(5_000_000))
        (tree / 'SYSTEM').write_bytes(os.urandom(9_000_017))          # not a cluster multiple
        (tree / 'dtb.img').write_bytes(b'dtb')
        (tree / 'KERNEL.md5').write_text('abc  target/KERNEL\n')
        (tree / 'empty').write_bytes(b'')
        (tree / 'extlinux').mkdir(); (tree / 'extlinux/extlinux.conf').write_text('LABEL x\n')
        (tree / 'overlays').mkdir(); (tree / 'overlays/sun50i-h700-anbernic-rg35xx-2024-ddr3.dtbo').write_bytes(b'o' * 281)
        return tree

    def test_populated_volume_is_clean_and_every_file_reads_back_identically(self):
        tree = self.sample_tree()
        work, image, offset = self.build(tree)
        part = work / 'part.fat'
        with open(image, 'rb') as f, open(part, 'wb') as g:
            f.seek(offset); shutil.copyfileobj(f, g)
        fsck = subprocess.run(['fsck.fat', '-n', '-v', str(part)], capture_output=True, text=True)
        self.assertEqual(fsck.returncode, 0, fsck.stdout + fsck.stderr)
        out = work / 'extract'
        subprocess.run(['7z', 'x', '-y', f'-o{out}', str(part)], check=True, capture_output=True)
        for src in tree.rglob('*'):
            if src.is_file():
                got = out / src.relative_to(tree)
                self.assertTrue(got.exists(), f'{src.relative_to(tree)} missing after round trip')
                self.assertEqual(hashlib.sha256(got.read_bytes()).hexdigest(), hashlib.sha256(src.read_bytes()).hexdigest(), src.name)

    def test_long_names_get_lfn_entries_and_unique_short_aliases(self):
        self.assertEqual(fatwrite.short_name('KERNEL', set())[0], b'KERNEL     ')
        self.assertFalse(fatwrite.short_name('KERNEL', set())[1])
        alias, lfn = fatwrite.short_name('dtb.img', set())
        self.assertTrue(lfn); self.assertEqual(alias, b'DTB~1   IMG')
        first = fatwrite.short_name('sun50i-h700-a.dtbo', set())[0]
        second = fatwrite.short_name('sun50i-h700-b.dtbo', {first.decode()})[0]
        self.assertNotEqual(first, second)

    def test_a_full_volume_stops_the_build(self):
        tree = Path(tempfile.mkdtemp(prefix='wispos-fat-src-')); (tree / 'big').write_bytes(b'x' * 40_000_000)
        with self.assertRaises(SystemExit):
            self.build(tree, size_mb=34)


if __name__ == '__main__':
    unittest.main()
