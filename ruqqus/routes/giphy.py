from flask import request, jsonify
from os import environ
import requests

from ruqqus.__main__ import app

GIPHY_KEY = (environ.get('GIPHY_KEY') or '').rstrip()


@app.route("/giphy", methods=["GET"])
@app.route("/giphy<path>", methods=["GET"])
def giphy(path=None):

    searchTerm = request.args.get("searchTerm", "")
    limit = request.args.get("limit", "")
    limit = min(int(limit), 48) if limit.isascii() and limit.isdigit() else 48

    params = {"api_key": GIPHY_KEY, "limit": limit or 48}
    if searchTerm:
        params["q"] = searchTerm
        url = "https://api.giphy.com/v1/gifs/search"
    else:
        url = "https://api.giphy.com/v1/gifs"
    return jsonify(requests.get(url, params=params, timeout=5).json())
