import requests
from requests_accelerator import FastHTTPAdapter, compare_hashes
import time
import io

SMALL_FILE = "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png"
LARGE_FILE = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"
LARGE_HASH = ["md5=yrCLNhle2xoSMdLQn6RQ4A==","crc32c=x4GOmQ=="]
MED_FILE = "https://archive.org/download/BigBuckBunny_328/BigBuckBunny_512kb.mp4"

def test_content_file():

    s = requests.Session()
    s.mount('http://', FastHTTPAdapter(connections=5, cache_dir="/tmp"))
    s.mount('https://', FastHTTPAdapter(connections=5, cache_dir="/tmp"))
    
    r = s.get(SMALL_FILE)
    assert(r.content == open(r.path, "rb").read())
    assert(len(r.content) == int(r.headers["content-length"]))
    assert(r.request.method == 'GET')

def test_stream_file():
    s = requests.Session()
    s.mount('https://', FastHTTPAdapter(connections=5, cache_dir="/tmp"))
    r = s.get(SMALL_FILE, stream=True)

    # TODO: test nothing downloaded yet?
    content = bytes()
    for chunk in r.iter_content():
        content += chunk
    assert(content == open(r.path, "rb").read())

def test_stream_ram():
    s = requests.Session()
    s.mount('https://', FastHTTPAdapter(connections=10, cache_dir=None))
    r = s.get(SMALL_FILE, stream=True)
    content = bytes()
    for chunk in r.iter_content():
        content += chunk
    assert(content == requests.get(SMALL_FILE).content)

def test_stream_ram_large():

    size = 1024*100
    url = MED_FILE
    s = requests.Session()
    s.mount('https://', FastHTTPAdapter(connections=15, cache_dir=None))
    s.mount('http://', FastHTTPAdapter(connections=15, cache_dir=None))
    start = time.time()
    r = s.get(url, stream=True)
    content = io.BytesIO()
    for chunk in r.iter_content(size):
        content.write(chunk)
    new_time = time.time() - start

    start = time.time()
    r = requests.get(url, stream=True)
    original = io.BytesIO()
    for chunk in r.iter_content(size):
        original.write(chunk)
    original_time = time.time() - start

    assert(content.getbuffer() == original.getbuffer())
    hashs = r.headers.get("x-goog-hash").split(",") if "x-goog-hash" in r.headers else None
    if hashs:
        assert(compare_hashes(original, hashs))
        assert(compare_hashes(content, hashs))
    assert(new_time < original_time)

def test_content_file_remove():
    # TODO: stream the content removing temp files after reading
    pass

