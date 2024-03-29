    
A download accelerator for requests [TransportAdapter](https://docs.python-requests.org/en/master/user/advanced/#transport-adapters) to speed up downloads from slow servers, for example archive.org.

You can take any existing use of requests and switch out the HTTPAdapter for the the FastHTTPAdapter

    >>> import requests, requests_accelerator, io, os
    >>> s = requests.Session()
    >>> s.mount('http://', requests_accelerator.FastHTTPAdapter())
    >>> url = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"

    >>> r = s.get(url)
    >>> requests_accelerator.compare_hashes(r.content, r.headers.get("x-goog-hash"))
    True

# Performance

Most web servers these days are fast and with a fast server parallel downloads are not needed
and will likely result in slighly slower download speeds. For slow servers or servers where
per connection speed limiting has been implimented parallel downloading can dramaticly increase
speeds.

You can adjust the number maximum number parralel connections used. Some servers may detect if too many connections are used for the same file or from the same IP and this will result in a slower download.

    >>> s.mount('http://', requests_accelerator.FastHTTPAdapter(connections=10))
    >>> r = s.get("https://archive.org/download/ween2021-12-11/05%20I%20Get%20a%20Little%20Taste%20of%20You.flac")
    >>> requests_accelerator.compare_hashes(r.content, r)
    True

You also set a max block size. Smaller blocks will result quicker progress on the earlier parts of the file
but may slow down the total download speed

    >>> # TODO

# Reducing memory use

The default will use memory to store the file like requests normally does.
You can use streaming to try and reduce RAM but this will still result in higher 
memory use than without the FastHTTPAdapter due to the way it downloads multiple parts
of the file in parrallel

    >>> s.mount('http://', requests_accelerator.FastHTTPAdapter())
    >>> r = s.get(url, stream=True)
    >>> content = io.BytesIO()
    >>> for chunk in r.iter_content(10240): 
    ...    _ = content.write(chunk)
    >>> requests_accelerator.compare_hashes(content, r.headers.get("x-goog-hash"))
    True

You can however reduce memory for large downloads by enabling file caching.
This will remove the files as soon as the response content is fully read.

    >>> s.mount('http://', requests_accelerator.FastHTTPAdapter(dir="/tmp"))
    >>> r = s.get(url, stream=True)
    >>> content = io.BytesIO()
    >>> for chunk in r.iter_content(10240): 
    ...    _ = content.write(chunk)
    >>> requests_accelerator.compare_hashes(content, r.headers.get("x-goog-hash"))
    True

You can optionally keep the cache files and avoid reading this data back into memory.
This method creates a single sparse file rather than many seperate temporary files, reducing IO.

    >>> s.mount('http://', requests_accelerator.FastHTTPAdapter(dir="/tmp", keep=True))
    >>> r = s.get(url)
    >>> with open(r.path, "rb") as fp:
    ...    requests_accelerator.compare_hashes(fp, r.headers.get("x-goog-hash"))
    True
    >>> os.remove(r.path)


You can control the filenames used when caching files

    >>> # TODO

# Compatibility

Note that if the server doesn't support range requests or if you use other verbs like HEAD or PUT then
the default HTTPAdapter will be used. You can also pass in a based HTTPAdapter which will be used internally for these requests.

    >>> s.mount('http://', requests_accelerator.FastHTTPAdapter())
    >>> r = s.head(url)
    >>> r.headers.get("x-goog-hash")
    'crc32c=x4GOmQ==, md5=yrCLNhle2xoSMdLQn6RQ4A=='

# Progress

Since progress in downloading is no longer linear you can use an alternative callback to give you actual progress on the download


    >>> def progress(url, saved, total):
    ...    print("o", end="")
    >>> s.mount('http://', requests_accelerator.FastHTTPAdapter(progress=progress))
    >>> r = s.get(url)
    oooooo...oooooo

you can also use a special iterator to provide progress

    >>> # TODO 
    # >>> done = 0
    # >>> for chunk, position in r.unordered_iter():
    # ...   progress = (done := done + len(chunk)) / r.header['content-length']

# Resuming

For large unreliable downloads you can control the behaviour in the case of a download failure. 
Enabling resume will save additional metadata and if the same request is retried it will attempt to
start where it left off. This obeys etag and last modified caching headers so will abandon a partial download if these have changed.
    >>> # TODO: 

# Comandline

There is a command line download standalone

    >>> # TODO

# Implimentation
Internally uses threads to maintain compatibility with python 2 and 3.


# Related libraries
- https://pypi.org/project/parfive/

Thanks to https://www.geeksforgeeks.org/simple-multithreaded-download-manager-in-python/ for base code to build on

