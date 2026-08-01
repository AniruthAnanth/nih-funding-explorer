import http.server, os, socketserver, functools, sys
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=os.path.abspath(root))
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", 8811), h) as s:
    print("serving", os.path.abspath(root), "on 8811", flush=True)
    s.serve_forever()
