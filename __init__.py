import requests, requests_accelerator
import typer
from tqdm import tqdm
import os

app = typer.Typer()
last_saved = 0

@app.command()
def main(url: str):
    s = requests.Session()
    # r = s.head(url)
    # total = int(r.headers.get('content-length', 0))
    with tqdm(desc=url.split('/')[-1], total=0, unit_scale=True, unit="Bytes") as pbar:
        def progress(url, saved, total):
            global last_saved
            if last_saved == 0:
                pbar.reset(total=total)
            pbar.update(saved - last_saved)
            last_saved = saved

        adapter = requests_accelerator.FastHTTPAdapter(keep=True, dir=".", progress=progress)
        s.mount('http://', adapter)
        s.mount('https://', adapter)

        r = s.get(url, stream=False)
    # with tqdm.wrapattr(open(os.devnull, "wb"), "write",
    #                miniters=1, desc=url.split('/')[-1],
    #                total=int(r.headers.get('content-length', 0))) as fout:
    #     for chunk in r.iter_content(chunk_size=4096):
    #         fout.write(chunk)



if __name__ == "__main__":
    app()