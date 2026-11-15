"""Decompression with a ceiling, for files a dump or a download supplies. zstd's largest ratio is about 32,000 to one (an RLE
block: 4 bytes to 128 KB; 64 MB of zeros pack into 2 kB), so a small crafted frame decompressed in one call is gigabytes held
at once. Here the input goes in STEP bytes at a time, so one call gives at most STEP * 32768 bytes (256 MB), and the output
stops at `most`.

    zstd(data, most, dictionary=None)   the first frame of data, at most `most` bytes, else TooLarge
"""
STEP = 8 << 10


class TooLarge(ValueError):
    pass


def zstd(data, most, dictionary=None):
    import zstandard
    d = zstandard.ZstdDecompressor(dict_data=zstandard.ZstdCompressionDict(dictionary)) if dictionary is not None \
        else zstandard.ZstdDecompressor()
    o = d.decompressobj()
    out, got, view = [], 0, memoryview(data)
    for at in range(0, len(view), STEP):
        b = o.decompress(view[at:at + STEP])
        got += len(b)
        if got > most:
            raise TooLarge("unpacks to more than %d bytes" % most)
        out.append(b)
        if o.eof:
            break
    return b"".join(out)
