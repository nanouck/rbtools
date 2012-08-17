import re

from rbtools.api.request import HttpRequest


RESOURCE_MAP = {}
LINKS_TOK = 'links'
_EXCLUDE_ATTRS = [LINKS_TOK, 'stat']


def _create(resource, data={}, *args, **kwargs):
    """Generate a POST request on a resource."""
    request = HttpRequest(resource._links['create']['href'], method='POST',
                          query_args=kwargs)

    for name, value in data.iteritems():
        request.add_field(name, value)

    return request


def _delete(resource, *args, **kwargs):
    """Generate a DELETE request on a resource."""
    return HttpRequest(resource._links['delete']['href'], method='DELETE',
                       query_args=kwargs)


def _get_self(resource, *args, **kwargs):
    """Generate a request for a resource's 'self' link."""
    return HttpRequest(resource._links['self']['href'], query_args=kwargs)


def _update(resource, data={}, *args, **kwargs):
    """Generate a PUT request on a resource."""
    request = HttpRequest(resource._links['update']['href'], method='PUT',
                          query_args=kwargs)

    for name, value in data.iteritems():
        request.add_field(name, value)

    return request


# This dictionary is a mapping of special keys in a resources links,
# to a name and method used for generating a request for that link.
# This is used to special case the REST operation links. Any link
# included in this dictionary will be generated separately, and links
# with a None for the method will be ignored.
SPECIAL_LINKS = {
    'create': ['create', _create],
    'delete': ['delete', _delete],
    'next': ['get_next', None],
    'prev': ['get_prev', None],
    'self': ['get_self', _get_self],
    'update': ['update', _update],
}


class Resource(object):
    """Defines common functionality for Item and List Resources.

    Resources are able to make requests to the Web API by returning an
    HttpRequest object. When an HttpRequest is returned from a method
    call, the transport layer will execute this request and return the
    result to the user.

    Methods for constructing requests to perform each of the supported
    REST operations will be generated automatically. These methods
    will have names corresponding to the operation (e.g. 'update()').
    An additional method for re-requesting the resource using the
    'self' link will be generated with the name 'get_self'. Each
    additional link will have a method generated which constructs a
    request for retrieving the linked resource.
    """
    _excluded_attrs = []

    def __init__(self, payload, url, token=None, **kwargs):
        self.url = url
        self._token = token
        self._payload = payload
        self._excluded_attrs = self._excluded_attrs + _EXCLUDE_ATTRS

        # Determine where the links live in the payload. This
        # can either be at the root, or inside the resources
        # token.
        if LINKS_TOK in self._payload:
            self._links = self._payload[LINKS_TOK]
        elif (token and isinstance(self._payload[token], dict) and
              LINKS_TOK in self._payload[token]):
            self._links = self._payload[token][LINKS_TOK]
        else:
            self._payload[LINKS_TOK] = {}
            self._links = {}

        # Add a method for each supported REST operation, and
        # retrieving 'self'.
        for link, method in SPECIAL_LINKS.iteritems():
            if link in self._links and method[1]:
                setattr(self, method[0],
                    lambda resource=self, meth=method[1], **kwargs:
                    meth(resource, **kwargs))

        # Generate request methods for any additional links
        # the resource has.
        for link, body in self._links.iteritems():
            if link not in SPECIAL_LINKS:
                setattr(self, "get_%s" % (link),
                        lambda url=body['href'], **kwargs: HttpRequest(
                            url, query_args=kwargs))


class ResourceItem(Resource):
    """The base class for Item Resources.

    Any resource specific base classes for Item Resources should
    inherit from this class. If a resource specific base class does
    not exist for an Item Resource payload, this class will be used to
    create the resource.

    The body of the resource is copied into the fields dictionary. The
    Transport is responsible for providing access to this data,
    preferably as attributes for the wrapping class.
    """
    _excluded_attrs = []

    def __init__(self, payload, url, token=None, **kwargs):
        super(ResourceItem, self).__init__(payload, url, token=token)
        self.fields = {}

        # Determine the body of the resource's data.
        if token is not None:
            data = self._payload[token]
        else:
            data = self._payload

        for name, value in data.iteritems():
            if name not in self._excluded_attrs:
                self.fields[name] = value


class CountResource(ResourceItem):
    """Resource returned by a query with 'counts-only' true.

    When a resource is requested using 'counts-only', the payload will
    not contain the regular fields for the resource. In order to
    special case all payloads of this form, this class is used for
    resource construction.
    """
    def __init__(self, payload, url, **kwargs):
        super(CountResource, self).__init__(payload, url, token=None)

    def get_self(self, **kwargs):
        """Generate an GET request for the resource list.

        This will return an HttpRequest to retrieve the list resource
        which this resource is a count for. Any query arguments used
        in the request for the count will still be present, only the
        'counts-only' argument will be removed
        """
        # TODO: Fix this. It is generating a new request
        # for a URL with 'counts-only' set to False, but
        # RB treats the  argument being set to any value
        # as true.
        kwargs.update({'counts_only': False})
        return HttpRequest(self.url, query_args=kwargs)


class ResourceList(Resource):
    """The base class for List Resources.

    Any resource specific base classes for List Resources should
    inherit from this class. If a resource specific base class does
    not exist for a List Resource payload, this class will be used to
    create the resource.

    Instances of this class will act as a sequence, providing access
    to the payload for each Item resource in the list. Iteration is
    over the page of item resources returned by a single request, and
    not the entire list of resources. To iterate over all item
    resources 'get_next()' or 'get_prev()' should be used to grab
    additional pages of items.
    """
    def __init__(self, payload, url, token=None, item_mime_type=None):
        super(ResourceList, self).__init__(payload, url, token=token)
        self._item_mime_type = item_mime_type

        if token:
            self._item_list = payload[self._token]
        else:
            self._item_list = payload

        self.num_items = len(self._item_list)
        self.total_results = payload['total_results']

    def __len__(self):
        return self.num_items

    def __nonzero__(self):
        return True

    def __getitem__(self, key):
        return self._item_list[key]

    def __iter__(self):
        return self._item_list.__iter__()

    def get_next(self, **kwargs):
        if 'next' not in self._links:
            raise StopIteration()

        return HttpRequest(self._links['next']['href'], query_args=kwargs)

    def get_prev(self, **kwargs):
        if 'prev' not in self._links:
            raise StopIteration()

        return HttpRequest(self._links['prev']['href'], query_args=kwargs)


class RootResource(ResourceItem):
    """The Root resource specific base class.

    Provides additional methods for fetching any resource directly
    using the uri templates. A method of the form "get_<uri-template-name>"
    is called to retrieve the HttpRequest corresponding to the
    resource. Template replacement values should be passed in as a
    dictionary to the values parameter.
    """
    _excluded_attrs = ['uri_templates']
    _TEMPLATE_PARAM_RE = re.compile('\{(?P<key>.*)\}')

    def __init__(self, payload, url, **kwargs):
        super(RootResource, self).__init__(payload, url, token=None)
        # Generate methods for accessing resources directly using
        # the uri-templates.
        for name, url in payload['uri_templates'].iteritems():
            attr_name = "get_%s" % name
            if not hasattr(self, attr_name):
                setattr(self, attr_name,
                        lambda url=url, **kwargs: self._get_template_request(
                            url, **kwargs))

    def _get_template_request(self, url_template, values, **kwargs):
        url = self._TEMPLATE_PARAM_RE.sub(
            lambda m: str(values[m.group('key')]),
            url_template)
        return HttpRequest(url, query_args=kwargs)

RESOURCE_MAP['application/vnd.reviewboard.org.root'] = RootResource


class DiffResource(ResourceItem):
    def get_patch(self):
        """ Returns unified diff content."""
        pass

RESOURCE_MAP['application/vnd.reviewboard.org.diff'] = DiffResource
