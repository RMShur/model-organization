import os
import os.path as osp
import six
import sys
import logging
import logging.config
import yaml
import glob
import fasteners
from collections import OrderedDict, defaultdict
import model_organization.utils as utils


docstrings = utils.docstrings


def _get_home():
    """Find user's home directory if possible.
    Otherwise, returns None.

    :see:  http://mail.python.org/pipermail/python-list/2005-February/325395.html

    This function is copied from matplotlib version 1.4.3, Jan 2016
    """
    try:
        if six.PY2 and sys.platform == 'win32':
            path = os.path.expanduser(b"~").decode(sys.getfilesystemencoding())
        else:
            path = os.path.expanduser("~")
    except ImportError:
        # This happens on Google App Engine (pwd module is not present).
        pass
    else:
        if os.path.isdir(path):
            return path
    for evar in ('HOME', 'USERPROFILE', 'TMP'):
        path = os.environ.get(evar)
        if path is not None and os.path.isdir(path):
            return path
    return None


def get_configdir(name):
    """
    Return the string representing the configuration directory.

    The directory is chosen as follows:

    1. If the ``name.upper() + CONFIGDIR`` environment variable is supplied,
       choose that.

    2a. On Linux, choose `$HOME/.config`.

    2b. On other platforms, choose `$HOME/.matplotlib`.

    3. If the chosen directory exists, use that as the
       configuration directory.
    4. A directory: return None.

    Notes
    -----
    This function is taken from the matplotlib [1] module

    References
    ----------
    [1]: http://matplotlib.org/api/"""
    configdir = os.environ.get('%sCONFIGDIR' % name.upper())
    if configdir is not None:
        return os.path.abspath(configdir)

    p = None
    h = _get_home()
    if ((sys.platform.startswith('linux') or
         sys.platform.startswith('darwin')) and h is not None):
        p = os.path.join(h, '.config/' + name)
    elif h is not None:
        p = os.path.join(h, '.' + name)

    if not os.path.exists(p):
        os.makedirs(p)
    return p


def setup_logging(default_path=None, default_level=logging.INFO,
                  env_key='LOG_IUN'):
    """
    Setup logging configuration

    Parameters
    ----------
    default_path: str
        Default path of the yaml logging configuration file. If None, it
        defaults to the 'logging.yaml' file in the config directory
    default_level: int
        Default: :data:`logging.INFO`. Default level if default_path does not
        exist
    env_key: str
        environment variable specifying a different logging file than
        `default_path` (Default: 'LOG_CFG')

    Returns
    -------
    path: str
        Path to the logging configuration file

    Notes
    -----
    Function taken from
    http://victorlin.me/posts/2012/08/26/good-logging-practice-in-python"""
    path = default_path or os.path.join(
        os.path.dirname(__file__), 'logging.yaml')
    value = os.getenv(env_key, None)
    home = _get_home()
    if value:
        path = value
    if os.path.exists(path):
        with open(path, 'rt') as f:
            config = yaml.load(f.read())
        for handler in config.get('handlers', {}).values():
            if '~' in handler.get('filename', ''):
                handler['filename'] = handler['filename'].replace(
                    '~', home)
        logging.config.dictConfig(config)
    else:
        path = None
        logging.basicConfig(level=default_level)
    return path


def ordered_yaml_load(stream, Loader=None, object_pairs_hook=OrderedDict):
    """Loads the stream into an OrderedDict.
    Taken from

    http://stackoverflow.com/questions/5121931/in-python-how-can-you-load-yaml-
    mappings-as-ordereddicts"""
    Loader = Loader or yaml.Loader

    class OrderedLoader(Loader):
        pass

    def construct_mapping(loader, node):
        loader.flatten_mapping(node)
        return object_pairs_hook(loader.construct_pairs(node))
    OrderedLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping)
    return yaml.load(stream, OrderedLoader)


def ordered_yaml_dump(data, stream=None, Dumper=None, **kwds):
    """Dumps the stream from an OrderedDict.
    Taken from

    http://stackoverflow.com/questions/5121931/in-python-how-can-you-load-yaml-
    mappings-as-ordereddicts"""
    Dumper = Dumper or yaml.Dumper

    class OrderedDumper(Dumper):
        pass

    def _dict_representer(dumper, data):
        return dumper.represent_mapping(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
            data.items())
    OrderedDumper.add_representer(OrderedDict, _dict_representer)
    return yaml.dump(data, stream, OrderedDumper, **kwds)


def safe_load(fname):
    """
    Load the file fname and make sure it can be done in parallel

    Parameters
    ----------
    fname: str
        The path name
    """
    lock = fasteners.InterProcessLock(fname + '.lck')
    lock.acquire()
    try:
        with open(fname) as f:
            return ordered_yaml_load(f)
    except:
        raise
    finally:
        lock.release()


def safe_dump(d, fname):
    """
    Savely dump `d` to `fname` using yaml

    This method creates a copy of `fname` called ``fname + '~'`` before saving
    `d` to `fname` using :func:`ordered_yaml_dump`

    Parameters
    ----------
    d: object
        The object to dump
    fname: str
        The path where to dump `d`
    """
    if osp.exists(fname):
        os.rename(fname, fname + '~')
    lock = fasteners.InterProcessLock(fname + '.lck')
    lock.acquire()
    try:
        with open(fname, 'w') as f:
            ordered_yaml_dump(d, f)
    except:
        raise
    finally:
        lock.release()


class Archive(six.text_type):
    """
    Just a dummy string subclass to identify archived experiments
    """

    #: The name of the project inside this archive
    project = None

    #: The time when this project has been archived
    time = None


class ExperimentsConfig(OrderedDict):

    paths = ['expdir', 'src', 'data', 'input', 'outdata', 'outdir',
             'plot_output', 'project_output']

    @property
    def exp_file(self):
        return osp.join(self.projects.conf_dir, 'experiments.yml')

    @property
    def project_map(self):
        """A mapping from project name to experiments"""
        # first update with new experiments
        for key, val in self.items():
            if (isinstance(val, dict) and
                    key not in self._project_map[val['project']]):
                self._project_map[val['project']].append(key)
            elif (isinstance(val, Archive) and
                  key not in self._project_map[val.project]):
                self._project_map[val.project].append(key)
        return self._project_map

    @property
    def exp_files(self):
        ret = OrderedDict()
        # restore the order of the experiments
        exp_file = self.exp_file
        if osp.exists(exp_file):
            for key, val in safe_load(exp_file).items():
                ret[key] = val
        for project, d in self.projects.items():
            project_path = d['root']
            config_path = osp.join(project_path, '.project')
            if not osp.exists(config_path):
                continue
            for fname in glob.glob(osp.join(config_path, '*.yml')):
                if fname == '.project.yml':
                    continue
                exp = osp.splitext(osp.basename(fname))[0]
                if not isinstance(ret.get(exp), Archive):
                    ret[exp] = osp.join(config_path, exp + '.yml')
                if exp not in self._project_map[project]:
                    self._project_map[project].append(exp)
        return ret

    def __init__(self, projects, d=None, project_map=None):
        super(ExperimentsConfig, self).__init__()
        self.projects = projects
        self._finalized = False
        self._project_map = project_map or defaultdict(list)
        if projects:
            if d is not None:
                for key, val in d.items():
                    self[key] = val
            else:
                # setup the paths for the experiments
                for key, val in self.exp_files.items():
                    self[key] = val
        self._finalized = False

    def __getitem__(self, attr):
        ret = super(ExperimentsConfig, self).__getitem__(attr)
        if not isinstance(ret, (dict, Archive)):
            fname = super(ExperimentsConfig, self).__getitem__(attr)
            self[attr] = d = safe_load(fname)
            if isinstance(d, dict):
                self.fix_paths(d)
            return d
        else:
            return ret

    def __setitem__(self, key, val):
        if isinstance(val, Archive):
            # make sure the project_map is up-to-date
            self.project_map
        super(ExperimentsConfig, self).__setitem__(key, val)

    def __reduce__(self):
        return self.__class__, (self.projects, OrderedDict(self),
                                self._project_map)

    @docstrings.get_sectionsf('ExperimentsConfig.fix_paths',
                              sections=['Parameters', 'Returns'])
    @docstrings.dedent
    def fix_paths(self, d, root=None, project=None):
        """
        Fix the paths in the given dictionary to get absolute paths

        Parameters
        ----------
        d: dict
            One experiment configuration dictionary
        root: str
            The root path of the project
        project: str
            The project name

        Returns
        -------
        dict
            The modified `d`

        Notes
        -----
        d is modified in place!"""
        if root is None and project is None:
            project = d.get('project')
            if project is not None:
                root = self.projects[project]['root']
            else:
                root = d['root']
        elif root is None:
            root = self.projects[project]['root']
        elif project is None:
            pass
        paths = self.paths
        for key, val in d.items():
            if isinstance(val, dict):
                d[key] = self.fix_paths(val, root, project)
            elif key in paths:
                val = d[key]
                if isinstance(val, six.string_types) and not osp.isabs(val):
                    d[key] = osp.join(root, val)
                elif (isinstance(utils.safe_list(val)[0], six.string_types) and
                      not osp.isabs(val[0])):
                    for i in range(len(val)):
                        val[i] = osp.join(root, val[i])
        return d

    @docstrings.get_sectionsf('ExperimentsConfig.rel_paths',
                              sections=['Parameters', 'Returns'])
    @docstrings.dedent
    def rel_paths(self, d, root=None, project=None):
        """
        Fix the paths in the given dictionary to get relative paths

        Parameters
        ----------
        %(ExperimentsConfig.fix_paths.parameters)s

        Returns
        -------
        %(ExperimentsConfig.fix_paths.returns)s

        Notes
        -----
        d is modified in place!"""
        if root is None and project is None:
            project = d.get('project')
            if project is not None:
                root = self.projects[project]['root']
            else:
                root = d['root']
        elif root is None:
            root = self.projects[project]['root']
        elif project is None:
            pass
        paths = self.paths
        for key, val in d.items():
            if isinstance(val, dict):
                d[key] = self.rel_paths(val, root, project)
            elif key in paths:
                val = d[key]
                if isinstance(val, six.string_types) and osp.isabs(val):
                    d[key] = osp.relpath(val, root)
                elif (isinstance(utils.safe_list(val)[0], six.string_types) and
                      osp.isabs(val[0])):
                    for i in range(len(val)):
                        val[i] = osp.relpath(val[i], root)
        return d

    def save(self):
        for exp, d in dict(self).items():
            if isinstance(d, dict):
                project_path = self.projects[d['project']]['root']
                self.rel_paths(d)
                fname = osp.join(project_path, '.project', exp + '.yml')
                if not osp.exists(osp.dirname(fname)):
                    os.makedirs(osp.dirname(fname))
                safe_dump(d, fname)
        exp_file = self.exp_file
        # to be 100% sure we do not write to the file from multiple processes
        lock = fasteners.InterProcessLock(exp_file + '.lck')
        lock.acquire()
        safe_dump(OrderedDict((exp, val if isinstance(val, Archive) else None)
                              for exp, val in self.items()), exp_file)
        lock.release()

    def load(self):
        for key in self:
            self[key]
        return self


class ProjectsConfig(OrderedDict):

    @property
    def all_projects(self):
        """The name of the configuration file"""
        return osp.join(self.conf_dir, 'projects.yml')

    def __init__(self, conf_dir, d=None):
        super(ProjectsConfig, self).__init__()
        self.conf_dir = conf_dir
        fname = self.all_projects
        if osp.exists(fname):
            self.project_paths = project_paths = safe_load(fname)
        else:
            self.project_paths = project_paths = OrderedDict()
        if d is not None:
            for key, val in d.items():
                self[key] = val
        else:
            for project, path in project_paths.items():
                self[project] = safe_load(
                    osp.join(path, '.project', '.project.yml'))

    def __reduce__(self):
        return self.__class__, (self.conf_dir, OrderedDict(self))

    def save(self):
        project_paths = OrderedDict()
        for project, d in OrderedDict(self).items():
            if isinstance(d, dict):
                project_path = d['root']
                fname = osp.join(project_path, '.project', '.project.yml')
                if not osp.exists(osp.dirname(fname)):
                    os.makedirs(osp.dirname(fname))
                if osp.exists(fname):
                    os.rename(fname, fname + '~')
                safe_dump(d, fname)
                project_paths[project] = project_path
            else:
                project_paths = self.project_paths[project]
        self.project_paths = project_paths
        safe_dump(project_paths, self.all_projects)


class Config(object):
    """Configuration class for one model organizer"""

    #: Boolean that is True when the experiments shall be synched with the
    #: files on the harddisk. Use the :meth:`save` method to store the
    #: configuration
    _store = False

    def __init__(self, name):
        self.name = name
        self.conf_dir = get_configdir(name)
        self.projects = ProjectsConfig(self.conf_dir)
        self.experiments = ExperimentsConfig(self.projects)
        self._globals_file = osp.join(self.conf_dir, 'globals.yml')
        if osp.exists(self._globals_file):
            self.global_config = safe_load(self._globals_file)
        else:
            self.global_config = OrderedDict()

    def save(self):
        self.projects.save()
        self.experiments.save()
        safe_dump(self.global_config, self._globals_file)


setup_logging()
