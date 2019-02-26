# -*- coding: utf-8 -*-
"""
A representation of a Locust Task.
"""
import json
from collections import OrderedDict
from json import JSONDecodeError
from types import MappingProxyType
from typing import (
    Iterable,
    NamedTuple,
    Iterator,
    Sequence,
    Optional,
    Mapping,
    Dict,
    List,
    Tuple,
    cast,
)

import dataclasses
from dataclasses import dataclass

import transformer.python as py
from transformer.blacklist import on_blacklist
from transformer.helpers import zip_kv_pairs
from transformer.request import HttpMethod, Request, QueryPair

IMMUTABLE_EMPTY_DICT = MappingProxyType({})
TIMEOUT = 30
ACTION_INDENTATION_LEVEL = 12
JSON_MIME_TYPE = "application/json"


class LocustRequest(NamedTuple):
    """
    All parameters for the request performed by the Locust client object.
    """

    method: HttpMethod
    url: str
    headers: Mapping[str, str]
    post_data: dict = MappingProxyType({})
    query: Sequence[QueryPair] = ()

    @classmethod
    def from_request(cls, r: Request) -> "LocustRequest":
        return LocustRequest(
            method=r.method,
            url=repr(r.url.geturl()),
            headers=zip_kv_pairs(r.headers),
            post_data=r.post_data,
            query=r.query,
        )


@dataclass
class Task2:
    name: str
    request: Request
    statements: Sequence[py.Statement] = ()
    # TODO: Replace me with a plugin framework that accesses the full tree.
    #   See https://github.com/zalando-incubator/Transformer/issues/11.
    global_code_blocks: Mapping[str, Sequence[str]] = IMMUTABLE_EMPTY_DICT

    def __post_init__(self,) -> None:
        self.statements = list(self.statements)
        self.global_code_blocks = {
            k: list(v) for k, v in self.global_code_blocks.items()
        }

    @classmethod
    def from_requests(cls, requests: Iterable[Request]) -> Iterator["Task2"]:
        """
        Generates a set of tasks from a given set of HTTP requests.
        Each request will be turned into an unevaluated function call making
        the actual request.
        The returned tasks are ordered by increasing timestamp of the
        corresponding request.
        """
        # TODO: Update me when merging Task with Task2: "statements" needs to
        #   contain a ExpressionView to Task2.request.
        #   See what is done in from_task (but without the LocustRequest part).
        #   See https://github.com/zalando-incubator/Transformer/issues/11.
        for req in sorted(requests, key=lambda r: r.timestamp):
            if not on_blacklist(req.url.netloc):
                yield cls(name=req.task_name(), request=req, statements=...)

    @classmethod
    def from_task(cls, task: "Task") -> "Task2":
        # TODO: Remove me as soon as the old Task is no longer used and Task2 is
        #   renamed to Task.
        #   See https://github.com/zalando-incubator/Transformer/issues/11.
        t = cls(name=task.name, request=task.request)
        if task.locust_request:
            expr_view = py.ExpressionView(
                name="this task's request field",
                target=lambda: task.locust_request,
                converter=lreq_to_expr,
            )
        else:
            expr_view = py.ExpressionView(
                name="this task's request field",
                target=lambda: t.request,
                converter=req_to_expr,
            )
        t.statements = [
            *[py.OpaqueBlock(x) for x in task.locust_preprocessing],
            py.Assignment("response", expr_view),
            *[py.OpaqueBlock(x) for x in task.locust_postprocessing],
        ]
        return t


NOOP_HTTP_METHODS = {HttpMethod.GET, HttpMethod.OPTIONS, HttpMethod.DELETE}


def req_to_expr(r: Request) -> py.FunctionCall:
    url = py.Literal(str(r.url.geturl()))
    args: Dict[str, py.Expression] = OrderedDict(
        url=url,
        name=url,
        headers=py.Literal(zip_kv_pairs(r.headers)),
        timeout=py.Literal(TIMEOUT),
        allow_redirects=py.Literal(False),
    )
    if r.method is HttpMethod.POST:
        rpd = RequestsPostData.from_har_post_data(r.post_data)
        args.update(rpd.as_kwargs())
    elif r.method is HttpMethod.PUT:
        rpd = RequestsPostData.from_har_post_data(r.post_data)
        args.update(rpd.as_kwargs())

        args.setdefault("params", py.Literal({}))
        cast(py.Literal, args["params"]).value.extend(
            _params_from_name_value_dicts([dataclasses.asdict(q) for q in r.query])
        )
    elif r.method not in NOOP_HTTP_METHODS:
        raise ValueError(f"unsupported HTTP method: {r.method!r}")

    method = r.method.name.lower()
    return py.FunctionCall(name=f"self.client.{method}", named_args=args)


def lreq_to_expr(lr: LocustRequest) -> py.FunctionCall:
    # TODO: Remove me once LocustRequest no longer exists.
    #   See https://github.com/zalando-incubator/Transformer/issues/11.
    if lr.url.startswith("f"):
        url = py.FString(lr.url[2:-1])
    else:
        url = py.Literal(lr.url[1:-1])

    args: Dict[str, py.Expression] = OrderedDict(
        url=url,
        name=url,
        headers=py.Literal(lr.headers),
        timeout=py.Literal(TIMEOUT),
        allow_redirects=py.Literal(False),
    )
    if lr.method is HttpMethod.POST:
        rpd = RequestsPostData.from_har_post_data(lr.post_data)
        args.update(rpd.as_kwargs())
    elif lr.method is HttpMethod.PUT:
        rpd = RequestsPostData.from_har_post_data(lr.post_data)
        args.update(rpd.as_kwargs())

        args.setdefault("params", py.Literal({}))
        cast(py.Literal, args["params"]).value.extend(
            _params_from_name_value_dicts([dataclasses.asdict(q) for q in lr.query])
        )
    elif lr.method not in NOOP_HTTP_METHODS:
        raise ValueError(f"unsupported HTTP method: {lr.method!r}")

    method = lr.method.name.lower()
    return py.FunctionCall(name=f"self.client.{method}", named_args=args)


class Task(NamedTuple):
    """
    One step of "doing something" on a website.
    This basically represents a @task in Locust-speak.
    """

    name: str
    request: Request
    locust_request: Optional[LocustRequest] = None
    locust_preprocessing: Sequence[str] = ()
    locust_postprocessing: Sequence[str] = ()
    global_code_blocks: Mapping[str, Sequence[str]] = MappingProxyType({})

    @classmethod
    def from_requests(cls, requests: Iterable[Request]) -> Iterator["Task"]:
        """
        Generates a set of Tasks from a given set of Requests.
        """

        for req in sorted(requests, key=lambda r: r.timestamp):
            if on_blacklist(req.url.netloc):
                continue
            else:
                yield cls(name=req.task_name(), request=req)

    def inject_headers(self, headers: dict):
        if self.locust_request is None:
            original_locust_request = LocustRequest.from_request(self.request)
        else:
            original_locust_request = self.locust_request

        new_locust_request = original_locust_request._replace(
            headers={**original_locust_request.headers, **headers}
        )
        task = self._replace(locust_request=new_locust_request)

        return task

    def replace_url(self, url: str):
        if self.locust_request is None:
            original_locust_request = LocustRequest.from_request(self.request)
        else:
            original_locust_request = self.locust_request

        new_locust_request = original_locust_request._replace(url=url)
        return self._replace(locust_request=new_locust_request)


@dataclass
class RequestsPostData:
    """
    Data to be sent via HTTP POST, along with which API of the requests library
    to use.
    """

    data: Optional[py.Literal] = None
    params: Optional[py.Literal] = None
    json: Optional[py.Literal] = None

    def as_kwargs(self) -> Dict[str, py.Expression]:
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}

    @classmethod
    def from_har_post_data(cls, post_data: dict) -> "RequestsPostData":
        """
        Converts a HAR postData object into a RequestsPostData instance.

        :param post_data: a HAR "postData" object,
            see http://www.softwareishard.com/blog/har-12-spec/#postData.
        :raise ValueError: if *post_data* is invalid.
        """
        try:
            return _from_har_post_data(post_data)
        except ValueError as err:
            raise ValueError(f"invalid HAR postData object: {post_data!r}") from err


def _from_har_post_data(post_data: dict) -> RequestsPostData:
    mime_k = "mimeType"
    try:
        mime: str = post_data[mime_k]
    except KeyError:
        raise ValueError(f"missing {mime_k!r} field") from None

    rpd = RequestsPostData()

    # The "text" and "params" fields are supposed to be mutually
    # exclusive (according to the HAR spec) but nobody respects that.
    # Often, both text and params are provided for x-www-form-urlencoded.
    text_k, params_k = "text", "params"
    if text_k not in post_data and params_k not in post_data:
        raise ValueError(f"should contain {text_k!r} or {params_k!r}")

    _extract_text(mime, post_data, text_k, rpd)

    try:
        params = _params_from_post_data(params_k, post_data)
        if params is not None:
            rpd.params = py.Literal(params)
    except (KeyError, UnicodeEncodeError, TypeError) as err:
        raise ValueError("unreadable params field") from err

    return rpd


def _extract_text(
    mime: str, post_data: dict, text_k: str, rpd: RequestsPostData
) -> None:
    text = post_data.get(text_k)
    if mime == JSON_MIME_TYPE:
        if text is None:
            raise ValueError(f"missing {text_k!r} field for {JSON_MIME_TYPE} content")
        try:
            rpd.json = py.Literal(json.loads(text))
        except JSONDecodeError as err:
            raise ValueError(f"unreadable JSON from field {text_k!r}") from err
    elif text is not None:  # Probably application/x-www-form-urlencoded.
        try:
            rpd.data = py.Literal(text.encode())
        except UnicodeEncodeError as err:
            raise ValueError(f"cannot encode the {text_k!r} field in UTF-8") from err


def _params_from_post_data(
    key: str, post_data: dict
) -> Optional[List[Tuple[bytes, bytes]]]:
    """
    Extracts the *key* list from *post_data* and calls
    _params_from_name_value_dicts with that list.

    :raise TypeError: if the object at *key* is built using unexpected data types.
    """
    params = post_data.get(key)
    if params is None:
        return
    if not isinstance(params, list):
        raise TypeError(f"the {key!r} field should be a list")
    return _params_from_name_value_dicts(params)


def _params_from_name_value_dicts(
    dicts: Iterable[Mapping[str, str]]
) -> List[Tuple[bytes, bytes]]:
    """
    Converts a HAR "params" element [0] into a list of tuples that can be used
    as value for requests' "params" keyword-argument.

    [0]: http://www.softwareishard.com/blog/har-12-spec/#params
    [1]: http://docs.python-requests.org/en/master/user/quickstart/
        #more-complicated-post-requests

    :raise KeyError: if one of the elements doesn't contain a "name" or "value" field.
    :raise UnicodeEncodeError: if an element's "name" or "value" string cannot
        be encoded in UTF-8.
    """
    return [(d["name"].encode(), d["value"].encode()) for d in dicts]
