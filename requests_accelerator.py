import requests
import io
import time
import threading
import os
from requests.adapters import HTTPAdapter
import hashlib
import datetime


class FastHTTPAdapter(HTTPAdapter):
    """
    a requests [TransportAdapter](https://docs.python-requests.org/en/master/user/advanced/#transport-adapters)
    to do parallel and resumable downloads for large files.

    Should support the following
    - not stream and cache in a given dir but only read into memory when you access r.content
    - stream to paritial files and remove them once read
    - stream but still cache full file?
    - no caching and just do it all in memory?
       - probably doesn't make sense since you normally want this for large downloads
    - resumability - leave partial downloads
    - ability to have custom filenames or random ones
    - no problem with other verbs
    - custom progress callback or iterator (since streaming for progress won't be very smooth)
       - e.g. for chunk, position in r.unordered_iter():

    You can fast download a file to a directory using requests by adding a custom TransportAdapter.

    >>> s = requests.Session()
    >>> s.mount('http://', FastHTTPAdapter(connections=5, dir="/tmp"))
    >>> s.mount('https://', FastHTTPAdapter(connections=5, dir="/tmp"))
    >>> url, hashes = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4", ["md5=yrCLNhle2xoSMdLQn6RQ4A==","crc32c=x4GOmQ=="]
    >>> url = "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png"

    If you don't access response.content or response.text it won't loaded it into memory and instead can be accessed 
    via response.path.

    >>> r = s.get(url)
    >>> r.content == open(r.path, "rb").read()
    True

    >>> len(r.content) == int(r.headers["content-length"])
    True

    >>> r.request.method
    'GET'

    Can also stream the content

    >>> r = s.get(url, stream=True)
    >>> content = bytes()
    # TODO: should not start downloading until here 
    >>> for chunk in r.iter_content():
    ...    content += chunk
    >>> content == open(r.path, "rb").read()
    True

    # TODO: stream content only in memory
    >>> s.mount('https://', FastHTTPAdapter(connections=5, dir=None))

    # TODO: stream the content removing temp files after reading

    # Related libraries
    - https://pypi.org/project/parfive/

    """

    def __init__(self, connections=5, dir=".", pool_connections=10, pool_maxsize=10, max_retries=0, pool_block=False):
        self.connections = connections
        super(FastHTTPAdapter, self).__init__(pool_connections, pool_maxsize, max_retries, pool_block)

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        if request.method != 'GET':
            response = super(FastHTTPAdapter, self).send(request, stream, timeout, verify, cert, proxies)
            return response
        else:
            response = download_file(request.url, number_of_threads=self.connections)
            return response

    # def build_fast_response(self, resp):
    #     response = Response()

    #     # Fallback to None if there's no status_code, for whatever reason.
    #     response.status_code = getattr(resp, 'status', None)

    #     # Make headers case-insensitive.
    #     response.headers = CaseInsensitiveDict(getattr(resp, 'headers', {}))

    #     # Set encoding.
    #     response.encoding = get_encoding_from_headers(response.headers)
    #     response.raw = resp
    #     response.reason = response.raw.reason

    #     if isinstance(req.url, bytes):
    #         response.url = req.url.decode('utf-8')
    #     else:
    #         response.url = req.url

    #     # Add new cookies from the server.
    #     extract_cookies_to_jar(response.cookies, req, resp)

    #     # Give the Response some context.
    #     response.request = req
    #     response.connection = self

    #     return response


def log(msg, level):
    # print("{}:{}".format(level, msg))
    pass


def download_file(url_of_file, name=None, number_of_threads=15, est_filesize=0, progress=None, chunk_size=1024, timeout=10, session=None):
    file_name = url_of_file.split('/')[-1] if not name else name
    session = session if session is not None else requests.Session()
    # Help make things more reliable esp if server is trying to block us or overloaded
    # TODO: perhaps handle this better by coming back to these connections at the end of the dl
    retries = requests.adapters.Retry(total=5,
                                      backoff_factor=0.1,
                                      status_forcelist=[500, 502, 503, 504, 499])
    session.mount(
        "http://", requests.adapters.HTTPAdapter(max_retries=retries))
    session.mount(
        "https://", requests.adapters.HTTPAdapter(max_retries=retries))
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
    state = {}
    ends = {}
    started = time.time()

    history = [(started, 0)]
    end = -1
    threads = []
    for i in range(number_of_threads):
        start = end + 1
        end = start + part
        if start > file_size and file_size != -1:  # due to min part size
            break
        if i+1 == number_of_threads or end > file_size:
            end = file_size
        state[start] = 0
        ends[start] = end

        def update_state(saved, start):
            state[start] = saved
            end = ends[start]
            last_update, last_saved = history[-1]
            if last_update is None:  # Special flag that dl is cancelled or exception happened
                # TODO: could be race condition if other thread adds to history without checking it first
                return (start, 0)
            if isinstance(saved, Exception):
                # flag to other threads to stop. #TODO: retry?
                history.append((None, saved))
                return (start, 0)
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
                    return (start, end)
                # will be picked up by this thread on its next update
                ends[sstart] = halfway-1
                # create a new state
                state[halfway], ends[halfway] = 0, send
                # tell the thread to switch to this
                return (halfway, send)

            dur = time.time() - last_update
            if progress is None or dur < 1:
                # don't update UI until every 1s
                return (start, end)
            # can have exceptions
            saved = sum([s for s in state.values() if type(s) == int])
            speed = (saved - last_saved)/1000**2/dur
            total_speed = saved/(time.time()-started)
            weighted_speed = (total_speed + speed*2)/3
            remain = 1/weighted_speed * (file_size-saved)
            est = time.time()-started + remain
            if progress(saved, file_size, "{:0.1f}MB/s {:0.0f}/{:0.0f}s".format(speed, remain, est), dict(state=state)) is not False:
                history.append((time.time(), saved))
                return (start, end)
            else:
                # TODO: Possibly might be better kept as an exception
                # set flag to stop all other threads
                history.append((None, saved))
                log("User cancelled download {}".format(url_of_file), "warning")
                return (start, 0)
        # create a Thread with start and end locations
        t = threading.Thread(target=Handler,
                             kwargs={'start': start, 'end': end, 'url': url_of_file, 'filename': file_name, 'progress': update_state, 'session': session, 'chunk_size': chunk_size})
        t.setDaemon(True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    # TODO: if cancelled we should delete the file
    last_update, last_saved = history[-1]
    if last_update is None:
        os.remove(file_name)
        r.headers['content-length'] = 0
    else:
        r.headers['content-length'] = file_size
    r.path = file_name
    # Reset the response
    r.request.method = "GET"
    r.raw = open(file_name, "rb")
    r._content = False
    r._content_consumed = False
    r._next = None
    r.elapsed = datetime.timedelta(seconds=last_update - started)

    return r


# based on https://www.geeksforgeeks.org/simple-multithreaded-download-manager-in-python/
def Handler(start, end, url, filename, progress, session, chunk_size):
    headers = {'Range': 'bytes=%d-%d' % (start, end)} if end > -1 else {}
    try:
        r = session.get(url, headers=headers,
                        stream=True, allow_redirects=True)
    except requests.exceptions.ConnectionError as e:
        log("Connection Error chunk {}-{}: {}".format(start, end, e), 'error')
        # Some errors get dealt with by the retry mechanism in the session
        # inc We get ConnectionError: ('Connection aborted.', BadStatusLine("''",))
        progress(e)  # will make the thread as bad
        # TODO: ensure these get retried again at the end?
        return
    # see https://stackoverflow.com/questions/29729082/python-fails-to-open-11gb-csv-in-r-mode-but-opens-in-r-mode
    with io.open(filename, "r+b") as fp:
        try:
            fp.seek(start, 0)
        except IOError as e:
            log("IOError seeking to start of write {}-{}: {}".format(start, end, e), "error")
            progress(e)
            return
        log("Starting Thread {}-{} fpos={}".format(start, end, fp.tell()), "debug")
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
                new_start, new_end = progress(saved, start)
                if new_start != start:
                    # We've been told to do another part
                    # TODO: flush or close?
                    log("Switching Thread {}-{}({}/{}) to {}-{}(0/{})".format(start, end,
                        saved, end-start, new_start, new_end, new_end-new_start), "debug")
                    Handler(new_start, new_end, url, filename,
                            progress, session, chunk_size)
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




def compare_hashes(filename, hashes):
    BUF_SIZE = 65536
    size = 0        
    libs = dict(sha1=hashlib.sha1(), md5=hashlib.md5(),crc32=crc32(),size=size_hash())
    with open(filename, 'rb') as f:
        while True:
            data = f.read(BUF_SIZE)
            size += len(data)
            if not data:
                break
            for lib in libs.values():
                lib.update(data)
    for hash in hashes:
        p = hash.split('=')
        if p[0] in libs:
            print (libs.get(p[0]).hexdigest(), p[1])