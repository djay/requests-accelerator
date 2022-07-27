import codecs
import requests
import io
import time
import threading
import os
from requests.adapters import HTTPAdapter
import hashlib
import datetime
from zlib import crc32


class FastHTTPAdapter(HTTPAdapter):

    def __init__(self, pool_connections=10, pool_maxsize=10, max_retries=0, pool_block=False, connections=5, keep=False, dir=None, progress=None):
        self.connections = connections
        self.dir = dir
        self.keep = keep
        self.progress = progress
        super(FastHTTPAdapter, self).__init__(pool_connections, pool_maxsize, max_retries, pool_block)

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        if request.method != 'GET':
            response = super(FastHTTPAdapter, self).send(request, stream, timeout, verify, cert, proxies)
            return response
        else:
            response = download_file(request.url, number_of_threads=self.connections, stream=stream, cache_dir=self.dir, keep=self.keep, progress=self.progress)
            return response


def log(msg, level):
    # print("{}:{}".format(level, msg))
    pass


def download_file(url_of_file, name=None, number_of_threads=15, est_filesize=0, progress=None, chunk_size=1024, timeout=10, session=None, stream=False, cache_dir=None, keep=False):
    if cache_dir is None:
        file_name = None
    else:
        file_name = url_of_file.split('/')[-1] if not name else name
        file_name = os.path.join(cache_dir, file_name)
    session = session if session is not None else requests.Session()
    # Help make things more reliable esp if server is trying to block us or overloaded
    # TODO: perhaps handle this better by coming back to these connections at the end of the dl
    # retries = requests.adapters.Retry(total=5,
    #                                   backoff_factor=0.1,
    #                                   status_forcelist=[500, 502, 503, 504, 499])
    # session.mount(
    #     "http://", requests.adapters.HTTPAdapter(max_retries=retries))
    # session.mount(
    #     "https://", requests.adapters.HTTPAdapter(max_retries=retries))
    # Get the size of the file
    r = session.head(url_of_file, allow_redirects=True, timeout=timeout)
    try:
        file_size = int(r.headers['content-length'])
    except:
        log("No filesize reported by server so can't continue download {}".format(
            url_of_file), "error")
        # TODO: might be able to still use multiple if we record parts that finish early?
        number_of_threads = 1
        #file_size = est_filesize # TODO: probably need to keep appending file instead?
        file_size = -1
    if r.headers.get('accept-ranges') != 'bytes':
        log("No 'accept-ranges' so can't continue download {}".format(url_of_file), "error")
        number_of_threads = 1
        file_size = -1
    # 2MB min so don't use lots of threads for small files
    part = max(int(file_size / number_of_threads), 2*2**20)
    if file_name is not None and keep:
        with io.open(file_name, "wb") as fp:
            try:
                fp.truncate(max(file_size, 0))
                log("Sparse file created {}bytes: {}".format(
                    file_size, file_name), 'notice')
            except IOError:
                log("Sparse file unsupported. Blanking out {}bytes: {}".format(
                    file_size, file_name), 'notice')
                # This is a lot slower
                i = 0
                started = time.time()
                block = 20*2**20
                while i < file_size:
                    fp.write(b'\0' * min(file_size-i, block))
                    i += block
                log("Blanked out {}bytes in {}s: {}".format(
                    file_size, time.time()-started, file_name), 'notice')
                # fp.truncate()

    # All keyed by start byte
    state = {}
    ends = {}
    files = {}

    started = time.time()

    history = [(started, 0)]
    end = -1
    threads = []
    is_updated = threading.Event()
    for i in range(number_of_threads):
        start = end + 1
        end = start + part
        if start > file_size and file_size != -1:  # due to min part size
            break
        if i+1 == number_of_threads or end > file_size:
            end = file_size
        state[start] = 0
        ends[start] = end

        #TODO: create files here or when thread finished, and pass in
        def get_fp(start, filename=file_name, single=False):
            if filename == None:
                return FifoFileBuffer()
            elif not single:
                fp = io.open("{}.{}".format(filename, start), "wb")
                return fp
            else:
                fp = io.open(filename, "r+b")
                try:
                    fp.seek(start, 0)
                except IOError as e:
                    log("IOError seeking to start of write {}-{}: {}".format(start, end, e), "error")
                    update(e, start)
                    return
                return fp


        def update_state(saved, start):
            """ Returns instructions to threads on what to work on next """
            state[start] = saved
            end = ends[start]
            is_updated.set()
            last_update, last_saved = history[-1]
            if last_update is None:  # Special flag that dl is cancelled or exception happened
                # TODO: could be race condition if other thread adds to history without checking it first
                return (start, 0, None)
            if isinstance(saved, Exception):
                # flag to other threads to stop. #TODO: retry?
                history.append((None, saved))
                return (start, 0, None)
            # TODO: if this thread finished we can send instructions to get half of another
            # threads part and tell that other part to get less. Prevents the slow down
            # caused by idle threads at the end.
            def remain(start): return ends[start]-start-state[start]
            if ends[start] == -1:
                # special case where we aren't doing range requests and want to keep going until the end
                pass
            elif remain(start) <= 0:
                # we finished. Find the slowest who has the most remaining and help them out
                history.append((time.time(), saved))
                sstart = sorted(state.keys(), key=remain, reverse=False).pop()
                # ensure slowest only gets half
                send = ends[sstart]
                halfway = sstart + state[sstart] + int(remain(sstart)/2)
                if send-halfway < 2**17:
                    # too small. Just end the thread
                    return (start, end, None)
                # will be picked up by this thread on its next update
                ends[sstart] = halfway-1
                # create a new state
                state[halfway], ends[halfway] = 0, send
                # tell the thread to switch to this
                fp = get_fp(halfway, file_name, not stream)
                files[halfway] = fp
                return (halfway, send, fp)

            dur = time.time() - last_update
            if progress is None or dur < 1:
                # don't update UI until every 1s
                return (start, end, files[start])
            # can have exceptions
            saved = sum([s for s in state.values() if type(s) == int])
            speed = (saved - last_saved)/1000**2/dur
            total_speed = saved/(time.time()-started)
            weighted_speed = (total_speed + speed*2)/3
            remain = 1/weighted_speed * (file_size-saved)
            est = time.time()-started + remain
            prog_desc = "{:0.1f}MB/s {:0.0f}/{:0.0f}s".format(speed, remain, est)
            if progress(url_of_file, saved, file_size) is not False:
                history.append((time.time(), saved))
                return (start, end, files[start])
            else:
                # TODO: Possibly might be better kept as an exception
                # set flag to stop all other threads
                history.append((None, saved))
                log("User cancelled download {}".format(url_of_file), "warning")
                return (start, 0, None)
        # create a Thread with start and end locations
        files[start] = get_fp(start, file_name, not stream)
        kwargs = {'fp':files[start], 'start': start, 'end': end, 'url': url_of_file, 'update': update_state, 'session': session, 'chunk_size': chunk_size}
        t = threading.Thread(target=Handler, kwargs=kwargs)
        t.setDaemon(True)
        t.start()
        threads.append(t)
    # TODO for streaming return with a virtual file that reads files as they are finished instead of blocking here
    if not stream: 
        for t in threads:
            t.join()
                # Try and get more
    # TODO: if cancelled we should delete the file

    # Adjust the head response to be GET with whole file
    last_update, last_saved = history[-1]
    if last_update is None and file_name:
        os.remove(file_name)
        r.headers['content-length'] = 0
    else:
        r.headers['content-length'] = file_size
    r.request.method = "GET"
    if not stream and file_name is not None and keep:
        r.path = file_name
        r.raw = open(file_name, "rb")
    else:
        r.raw = CombineFiles(files, state, ends, is_updated)
    r._content = False
    r._content_consumed = False
    r._next = None
    r.elapsed = datetime.timedelta(seconds=last_update - started)

    return r


def Handler(fp, start, end, url, update, session, chunk_size):
    """ request a range of the url until end or instructed by update to switch to a new range """
    headers = {'Range': 'bytes=%d-%d' % (start, end)} if end > -1 else {}
    try:
        r = session.get(url, headers=headers,
                        stream=True, allow_redirects=True)
    except requests.exceptions.ConnectionError as e:
        log("Connection Error chunk {}-{}: {}".format(start, end, e), 'error')
        # Some errors get dealt with by the retry mechanism in the session
        # inc We get ConnectionError: ('Connection aborted.', BadStatusLine("''",))
        update(e, start)  # will make the thread as bad
        # TODO: ensure these get retried again at the end?
        return
    # see https://stackoverflow.com/questions/29729082/python-fails-to-open-11gb-csv-in-r-mode-but-opens-in-r-mode
    # log("Starting Thread {}-{} fpos={}".format(start, end, fp.tell()), "debug")
    saved = 0
    last_update = time.time()
    for block in r.iter_content(chunk_size):
        remain = end-start-saved-len(block)
        #if remain < 0:
        #    block = block[:remain]
        fp.write(block)
        saved += len(block)
        #if saved > end-start:
        #    break
        #    #raise Exception()
        if remain <= 0 or time.time() - last_update > 0.5:
            fp.flush()
            last_update = time.time()
            new_start, new_end, new_fp = update(saved, start)
            if new_start != start:
                # We've been told to do another part
                # TODO: flush or close?
                log("Switching Thread {}-{}({}/{}) to {}-{}(0/{})".format(start, end,
                    saved, end-start, new_start, new_end, new_end-new_start), "debug")
                Handler(new_fp, new_start, new_end, url,
                        update, session, chunk_size)
                break
            elif new_end != -1 and new_end <= new_start:
                # User cancelled or exception in other thread
                log("Cancelling Thread {}-{}({}/{}) fpos={}".format(start,
                    end, saved, end-start, fp.tell(), ), "debug")
                break
            elif new_end == end and remain <= 0 and new_end > -1:
                log("Stopping Thread {}-{}({}/{}) fpos={}".format(start,
                    end, saved, end-start, fp.tell(), ), "debug")
                break
            elif new_end < end:
                # another thread is doing our work
                log("Shorten Thread {}-{}({}/{}) to {}-{}({}/{}) fpos={}".format(start, end, saved,
                    end-start, new_start, new_end, saved, new_end-new_start, fp.tell()), "debug")
                end = new_end
            else:
                # Continue on
                pass

class CombineFiles(io.IOBase):
    """A virtual file that reads and closes many files making up the whole download"""
    def __init__(self, files, state, ends, is_updated):
        self.files = files
        self.state = state
        self.ends = ends
        self.is_updated = is_updated

        self.start = 0
        self.pos = 0
        self.fp = None

    def read(self, _bytes):
        data = bytes()
        while self.start is not None:
            # get cur file and check if its ready
            block_pos = self.state.get(self.start)
            if block_pos < self.pos:
                # if not ready yet then wait
                self.is_updated.wait(0.1)
                self.is_updated.clear()
                continue
            if self.fp is None:
                # Open new fp for reading
                fp = self.files[self.start]
                if getattr(fp, 'name', None):
                    self.fp = open(fp.name, "rb")
                else:
                    self.fp = fp
                
            end = self.ends.get(self.start)
            # we have more to read
            to_read = min(_bytes - len(data), block_pos - self.pos, end + 1 - self.start - self.pos)
            read = self.fp.read(to_read)
            data += read
            self.pos += len(read)
            if self.pos >= (end - self.start):
                # Reached the end
                if self.fp:
                    self.fp.close()
                    self.files[self.start].close()
                    if hasattr(self.files[self.start], "name"):
                        os.remove(self.files[self.start].name)
                self.fp = None
                self.pos = 0
                self.start = next((s for s in sorted(self.files.keys()) if s > self.start), None)
                if self.start is None:
                    return data
            if len(data) >= _bytes:
                # print(".", end="")
                return data
        return bytes()


class FifoFileBuffer(object):
    """https://stackoverflow.com/questions/10917581/efficient-fifo-queue-for-arbitrarily-sized-chunks-of-bytes-in-python"""
    # TODO: make this thread safe
    def __init__(self):
        self.buf = io.BytesIO()
        self.available = 0    # Bytes available for reading
        self.size = 0
        self.write_fp = 0
        self.total_written = 0
        self._lock = threading.RLock()

    def read(self, size = None):
        """Reads size bytes from buffer"""
        with self._lock:
            if size is None or size > self.available:
                size = self.available
            size = max(size, 0)

            result = self.buf.read(size)
            self.available -= size

            if len(result) < size:
                self.buf.seek(0)
                result += self.buf.read(size - len(result))

        return result


    def write(self, data):
        """Appends data to buffer"""
        with self._lock:
            if self.size < self.available + len(data):
                # Expand buffer
                new_buf = io.BytesIO()
                new_buf.write(self.read())
                self.write_fp = self.available = new_buf.tell()
                read_fp = 0
                while self.size <= self.available + len(data):
                    self.size = max(self.size, 1024) * 2
                new_buf.write(b'0' * (self.size - self.write_fp))
                self.buf.close()
                self.buf = new_buf
            else:
                read_fp = self.buf.tell()

            self.buf.seek(self.write_fp)
            written = self.size - self.write_fp
            self.buf.write(data[:written])
            self.write_fp += len(data)
            self.available += len(data)
            if written < len(data):
                self.write_fp -= self.size
                self.buf.seek(0)
                self.buf.write(data[written:])
            self.buf.seek(read_fp)
            self.total_written += written
    
    def flush(self):
        pass

    def tell(self):
        return self.total_written

    def close(self):
        self.buf.close()



class ReadOverWriteFile(object):
    """
    Opens a file for reading and can be written as it's being read.

    Create a dummy file

    >>> import tempfile
    >>> from contextlib import closing
    >>> temp_name = next(tempfile._get_candidate_names())
    >>> with open(temp_name, "w") as fp:
    ...     _ = fp.writelines(['name1,name2\\r\\n'] + (['foo,bar\\r\\n'] * 3))

    Reopen it and rewrite it as we go

    >>> with closing(ReadOverWriteFile(temp_name)) as fp:
    ...    for line in fp:
    ...       _ = fp.write(line.replace("foo",""))
    >>> print(open(temp_name).read())
    name1,name2
    ,bar
    ,bar
    ,bar
    <BLANKLINE>

    More complicated test with csv.writer
    >>> import csv
    >>> with closing(ReadOverWriteFile(temp_name)) as fp:
    ...    reader = csv.DictReader(fp)
    ...    writer = csv.DictWriter(fp, fieldnames=reader.fieldnames, newline="\\n")
    ...    _ = writer.writeheader()
    ...    for num, line in enumerate(fp):
    ...           # _ = writer.writerow(dict(name2="f"))
    ...           _ = fp.write(line.replace("bar","f"))
    >>> print(open(temp_name).read())
    name1,name2
    ,f
    ,f
    ,f
    <BLANKLINE>

    But if we make the lines longer we get problems
    >>> with closing(ReadOverWriteFile(temp_name)) as fp:
    ...    for line in fp:
    ...        _ = fp.write(line.replace("f", "foobarfoobar"))
    Traceback (most recent call last):
     ...
    OSError: Can't write more than you read
    """

    def __init__(self, path, **params):
        self.buf = codecs.open(path, "r+", **params)
        self.write_fp = 0
        self.temp_buf = None

    def read(self, size = None):
        # if self.temp_buf is not None:
        #     data = self.temp_buf.read(size)
        #     if size is None or len(data) < size:
        #         self.temp_buf = None
        #         data += self.buf.read(None if size is None else size - len(data))
        #     return data
        return self.buf.read(size)

    def readline(self, size = -1):
        return self.buf.readline(size)
    
    def __iter__(self):
        while True:
            line = self.readline()
            if line == '':
                break
            yield line

    def write(self, data):
        read_fp = self.buf.tell()
        self.buf.seek(self.write_fp)
        # written = len(data)
        # overlap = read_fp - self.writefp + written
        # if overlap > 0:
        #     if self.temp_buf is None:
        #         self.temp_buf = io.StringIO()
        #     tread_fp = self.temp_buf.tell()
        #     self.temp_buf.seek(-1)
        #     self.temp_buf.write(self.buf.read(overlap))
        #     self.temp_buf.seek(tread_fp)
        self.buf.write(data)
        written = self.buf.tell() - self.write_fp 
        self.write_fp = self.buf.tell()
        if self.write_fp > read_fp:
            raise IOError("Can't write more than you read")
        self.buf.seek(read_fp)
        return written

    def writelines(self, lines):
        for data in lines:
            self.write(data)

    def flush(self):
        self.buf.flush()

    def tell(self):
        return self.write_fp

    def close(self):
        self.buf.truncate(self.write_fp)
        self.buf.close()


class size_hash(object):
    def update(self, buffer):
        self.buffer = buffer

    def hexdigest(self):
        return str(len(self.buffer))

class crc32_hash(object):
    def update(self, buffer):
        self.buffer = buffer
        
    def hexdigest(self):
        return crc32(self.buffer)


def gen_hashes(file):
    """ return dict for callables that will create a hash for this file """
    BUF_SIZE = 65536
    size = 0        
    libs = dict(
        sha1=hashlib.sha1(), 
        md5=hashlib.md5(),
        crc32=crc32_hash(),
        size=size_hash(),
        )
    if type(file) == str:
        file = open(file, "rb")
    if type(file) == bytes:
        data = file
    else:
        with file as f:
            data = f.read()

    if not data:
        return {}
    for lib in libs.values():
        lib.update(data)
    return libs

def compare_hashes(file, hashes):
    libs = gen_hashes(file)
    if hasattr(hashes, "headers"):
        request = hashes
        hashes = request.headers.get("x-goog-hash")
        if hashes is None:
            hashes = f"size={request.headers['content-length']}"
    if type(hashes) == str:
        hashes = hashes.split(",")

    for hash in hashes:
        p = hash.split('=')
        if p[0] in libs:
            # print (libs.get(p[0]).hexdigest(), p[1])
            if libs.get(p[0]).hexdigest() != p[1]:
                return False
    return True


def compute_hashes(file):
    libs = gen_hashes(file)
    for name, lib in libs.items():
        yield f"{name}={lib.hexdigest()}"
    return
