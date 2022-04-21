
A download accelerator for requests [TransportAdapter](https://docs.python-requests.org/en/master/user/advanced/#transport-adapters) to speed up downloads from slow servers, for example archive.org.

You can take any existing use of requests and switch out the HTTPAdapter for the the FastHTTPAdapter

>>> import requests, requests_accelerator
>>> s = requests.Session()
>>> s.mount('http://', requests_accelerator.FastHTTPAdapter())
>>> url = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"

>>> r = s.get(url)
>>> requests_accelerator.compare_hashes(r.content, r.headers.get("x-goog-hash"))
True

You can adjust the number maximum number parralell downloads used. Some servers may detect if too many connections are used for the same file or from the same IP and this will result in a slower download.

# TODO

You also set a max block size. Smaller blocks will result quicker progress on the earlier parts of the file
but may slow down the total download speed

# TODO


By default this will use memory to store the file like requests normally does.
You can use streaming to try and reduce RAM but this will still result in higher 
memory use than without the FastHTTPAdapter due to the way to downloads multiple parts
of the file at once

>>> r = s.get(url, stream=True)
>>> content = io.BytesIO()
>>> for chunk in r.iter_content(1024):
...    content.write(chunk)
>>> verify_hash(content, r)
True

You can however reduce memory for large downloads by enabling file caching.
By default this will remove the file as soon as the response content is fully read.
# TODO

You can optionally keep the cache files and avoid reading this data back into memory.
# TODO

If you cache the files and use streaming many files are created and then joined at the end.
Without streaming a single sparse file is created at the start avoiding additional IO and diskspace
to join the file at the end.

# TODO

You can control the filenames used when caching files

# TODO

For large unreliable downloads you can control the behaviour in the case of a download failure. 
Enabling resume will save additional metadata and if the same request is retried it will attempt to
start where it left off. This obeys etag and last modified caching headers so will abandon a partial download if these have changed.
# TODO: 

Note that if the server doesn't support range requests or if you use other verbs like HEAD or PUT then
the default HTTPAdapter will be used. You can also pass in a based HTTPAdapter which will be used for all internally for these requests.

# TODO

Since progress in downloading is no longer linear you can use an alternative callback to give you actual progress on the download

# TODO

you can also use an iterator to provide progress

# TODO 
>>> done = 0
>>> for chunk, position in r.unordered_iter():
>>>    progress = (done := done + len(chunk)) / r.header['content-length']


## Comandline

There is a command line download standalone

# TODO


## Implimentation
Internally uses threads to maintain compatibility with python 2 and 3.


# Related libraries
- https://pypi.org/project/parfive/

Thanks to https://www.geeksforgeeks.org/simple-multithreaded-download-manager-in-python/ for base code to build on

