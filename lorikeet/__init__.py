from flask import Flask, Blueprint

_SCRIPT_NAME = '/lorikeet'

class ReverseProxied(object):
    '''Wrap the application in this middleware and configure the 
    front-end server to add these headers, to let you quietly bind 
    this to a URL other than / and to an HTTP scheme that is 
    different than what is used locally.

    :param app: the WSGI application
    '''
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        script_name = _SCRIPT_NAME
        if script_name:
            environ['SCRIPT_NAME'] = script_name
            path_info = environ['PATH_INFO']
            if path_info.startswith(script_name):
                environ['PATH_INFO'] = path_info[len(script_name):]

        scheme = environ.get('HTTP_X_SCHEME', '')
        if scheme:
            environ['wsgi.url_scheme'] = scheme
        return self.app(environ, start_response)

app = Flask(__name__)
app.wsgi_app = ReverseProxied(app.wsgi_app)

# Custom filters for jinja2.

# Mapping for language codes for prismjs.
_PRISMJS_LANG_MAP = {
    'C': 'c',
    'C++': 'cpp',
    'Caml': 'cpp',
    'Haskell': 'haskell',
    'Java': 'java',
    'Pascal': 'pascal',
    'PHP': 'php',
    'Python': 'python',
}

app.jinja_env.filters['prism_lang_map'] = (
    lambda x:_PRISMJS_LANG_MAP.get(x) or 'cpp'
)

# Color class names for submissions scores. score: < 0 for no attempt
def get_color_tag(score):
    if score is None or score < 0:
        return "score_no_attempt"
    elif score == 0:
        return "score_0"
    elif score == 100:
        return "score_100"
    else:
        score_band = (score/10)*10
        return ("score_%d_%d" % (score_band, score_band+10))
app.jinja_env.filters['mark_color_class'] = get_color_tag

# Any filter.
app.jinja_env.filters['any'] = any

import lorikeet.views
