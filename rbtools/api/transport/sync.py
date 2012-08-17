from rbtools.api.decode import decode_response
from rbtools.api.factory import create_resource
from rbtools.api.request import HttpRequest, ReviewBoardServer
from rbtools.api.resource import ResourceItem, ResourceList
from rbtools.api.transport import Transport


LINK_KEYS = set(['href', 'method', 'title'])


class SyncTransport(Transport):
    """A synchronous transport layer for the API client.

    the url, cookie_file, username, and password parameters are
    mandatory when using this resource. The file provided in
    cookie_file is used to store and retrieive the authentication
    cookies for the API.

    The optional agent parameter can be used to specify a custom
    User-Agent string for the API. If not provided, the default
    RBTools User-Agent will be used.
    """
    def __init__(self, url, cookie_file, username, password, agent=None):
        super(SyncTransport, self).__init__(url)
        self.server = ReviewBoardServer(self.url, cookie_file,
                                        username=username, password=password)

        self.get_root = SyncTransportMethod(self, self._root_request)

    def _root_request(self):
        return HttpRequest(self.server.url)

    def wrap(self, value):
        """Wrap any values returned to the user

        All values returned from the transport should be wrapped with
        this method, unless the specific type is known and handled as
        a special case. This wrapping allows for nested dictionaries
        and fields to be accessed as attributes, instead of using the
        '[]' operation.

        This wrapping is also necessary to have control over updates to
        nested fields inside the resource.
        """
        if isinstance(value, ResourceItem):
            return SyncTransportItemResource(self, value)
        elif isinstance(value, ResourceList):
            return SyncTransportListResource(self, value)
        elif isinstance(value, list):
            return ResourceListField(self, value)
        elif isinstance(value, dict):
            dict_keys = set(value.keys())
            if ('href' in dict_keys and
                len(dict_keys.difference(LINK_KEYS)) == 0):
                return SyncTransportResourceLink(self, **value)
            else:
                return ResourceDictField(self, value)
        else:
            return value


class ResourceDictField(object):
    """Wrapper for dictionaries returned from a resource.

    Any dictionary returned from a resource will be wrapped using this
    class. Attribute access will correspond to accessing the
    dictionary key with the name of the attribute.
    """
    def __init__(self, transport, fields_dict):
        object.__setattr__(self, '_transport', transport)
        object.__setattr__(self, '_fields_dict', fields_dict)

    def __getattr__(self, name):
        fields = object.__getattribute__(self, '_fields_dict')
        transport = object.__getattribute__(self, '_transport')

        if name in fields:
            return transport.wrap(fields[name])
        else:
            raise AttributeError

    def __setattr__(self, name, value):
            object.__getattribute__(self, '_fields_dict')[name] = value


class SyncTransportListIterator(object):
    """Iterator for lists which uses __getitem__."""
    def __init__(self, l):
        self._list = l
        self.index = 0

    def __iter__(self):
        return self

    def next(self):
        try:
            item = self._list[self.index]
        except IndexError:
            raise StopIteration

        self.index += 1
        return item


class ResourceListField(list):
    """Wrapper for lists returned from a resource.

    Acts as a normal list, but wraps any returned items using the
    transport.
    """
    def __init__(self, transport, list_field):
        super(ResourceListField, self).__init__(list_field)
        self._transport = transport

    def __getitem__(self, key):
        item = super(ResourceListField, self).__getitem__(key)
        return self._transport.wrap(item)

    def __iter__(self):
        return SyncTransportListIterator(self)


class SyncTransportResourceLink(object):
    """Wrapper for links returned from a resource.

    In order to support operations on links found outside of a
    resource's links dictionary, detected links are wrapped with this
    class.

    A links fields (href, method, and title) are accessed as
    attributes, and link operations are supported through method
    calls. Currently the only supported method is "GET", which can be
    invoked using the 'get' method.
    """
    def __init__(self, transport, href, method="GET", title=None):
        self.href = href
        self.method = method
        self.title = title
        self.get = SyncTransportMethod(transport, self._get)
        # TODO: Might want to add support for methods other than "GET".

    def _get(self):
        return HttpRequest(self.href)


class SyncTransportItemResource(object):
    """Wrapper for Item resources.

    Provides access to an item resource's data, and methods. To
    retrieve a field from the resource's dictionary, the attribute with
    name equal to the key should be accessed.

    Any attributes which correspond to a resource method will be
    wrapped, and calling the method will correspond to executing the
    returned request.
    """
    _initted = False

    def __init__(self, transport, resource):
        self._transport = transport
        self._resource = resource

        # Indicate initialization is complete so that future
        # setting of attributes is done on the resource.
        self._initted = True

    def __getattr__(self, name):
        resource = object.__getattribute__(self, '_resource')
        if name in resource.fields:
            resource_attr = resource.fields[name]
        else:
            resource_attr = resource.__getattribute__(name)

        transport = object.__getattribute__(self, '_transport')

        if callable(resource_attr):
            return SyncTransportMethod(transport, resource_attr)

        return transport.wrap(resource_attr)

    def __setattr__(self, name, value):
        if not object.__getattribute__(self, '_initted'):
            object.__setattr__(self, name, value)
        else:
            resource = object.__getattribute__(self, '_resource')

            if resource and name in resource.fields:
                resource.fields[name] = value
            else:
                raise AttributeError


class SyncTransportListResource(object):
    """Wrapper for List resources.

    Acts as a sequence, providing access to the page of items. When a
    list item is accessed, the item resource will be constructed using
    the payload for the item, and returned.

    Iteration is over the page of item resources returned by a single
    request, and not the entire list of resources. To iterate over all
    item resources 'get_next()' or 'get_prev()' should be used to grab
    additional pages of items.
    """
    def __init__(self, transport, resource):
        self._transport = transport
        self._resource = resource
        self._item_cache = {}

    def __getattr__(self, name):
        resource = object.__getattribute__(self, '_resource')
        resource_attr = resource.__getattribute__(name)

        if callable(resource_attr):
            return SyncTransportMethod(self._transport, resource_attr)

        return self._transport.wrap(resource_attr)

    def __getitem__(self, key):
        if key in self._item_cache:
            return self._item_cache[key]

        payload = self._resource[key]

        # TODO: A proper url for the resource should be passed
        # in to the factory.
        resource = create_resource(
            payload,
            '',
            mime_type=self._resource._item_mime_type,
            guess_token=False)

        wrapped_resource = self._transport.wrap(resource)
        self._item_cache[key] = wrapped_resource
        return wrapped_resource

    def __iter__(self):
        return SyncTransportListIterator(self)


class SyncTransportMethod(object):
    """Wrapper for resource methods.

    Intercepts method return values, and synchronously executes all
    returned HttpRequests. If a method returns an HttpRequest the
    resulting response will be constructed into a new resource and
    returned.
    """
    def __init__(self, transport, method):
        self._transport = transport
        self._method = method

    def __call__(self, *args, **kwargs):
        """Executed when a resource's method is called."""
        call_result = self._method(*args, **kwargs)
        if not isinstance(call_result, HttpRequest):
            return call_result

        rsp = self._transport.server.make_request(call_result)
        info = rsp.info()
        mime_type = info['Content-Type']
        item_content_type = info.get('Item-Content-Type', None)
        payload = rsp.read()
        payload = decode_response(payload, mime_type)

        resource = create_resource(payload, call_result.url,
                                   mime_type=mime_type,
                                   item_mime_type=item_content_type)

        return self._transport.wrap(resource)
