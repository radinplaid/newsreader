# Security and deployment

## Who may talk to the API

The API answers the browser that serves the web client, and nobody else. A page
on any other website that the user visits cannot read from the server or change
the library through it: browsers send the `Origin` header on every write, and the
server refuses the ones it does not recognise.

The `web/` folder can still be hosted anywhere (GitHub Pages, S3, …) and pointed
at a running server, because the client is purely static. Name the origin it is
served from in `NR_ALLOWED_ORIGINS`, and that origin is let in:

```bash
NR_ALLOWED_ORIGINS="https://reader.example.com" python -m api
```

Without it, the server only answers its own origin. `NR_ALLOWED_ORIGINS=*` allows
every origin and removes the protection. The server says so when it starts.
Requests that carry no `Origin` header are not from a browser (curl, scripts) and
are always answered.

## Deploying behind a proxy or on another hostname

The server decides whether a page is its own by comparing the host in the
browser's `Origin` header with the `Host` header the request arrives with. The
scheme is not compared, so a TLS-terminating proxy is fine. The host and port
must match what the browser sees.

* **The proxy must forward the original `Host`.** nginx's `proxy_pass` sends the
  upstream address as `Host` unless you ask otherwise, which makes every write
  from the bundled UI fail. Use `proxy_set_header Host $host;`. Caddy's
  `reverse_proxy` forwards `Host` unchanged by default.
* **Reaching the server under another name is a different origin.**
  `http://localhost:8000`, `http://192.168.1.10:8000` and
  `https://reader.example.com` are three origins. The page still loads and lists
  items, but dismissing, starring, marking read or refreshing fails with
  `origin not allowed`. List every such origin in `NR_ALLOWED_ORIGINS`, or open
  the server under one name only.
* **Check a setup with a preflight request.** It writes nothing:

  ```bash
  curl -i -X OPTIONS -H 'Origin: https://reader.example.com' \
       -H 'Access-Control-Request-Method: POST' http://127.0.0.1:8000/api/categories
  ```

  `200` with `Access-Control-Allow-Origin` means the origin is accepted. `403`
  means it is refused.
