def imp(self, url, path, out=None, rev=None):
    erepo = {"url": url}
    if rev is not None:
        erepo["rev"] = rev
        path = path.lstrip("/")

    return self.imp_url(path, out=out, erepo=erepo, locked=True)
