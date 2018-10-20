# -*- coding: utf-8 -*-
# @author: RaNaN, mkaay

import builtins
from builtins import _, object, str
from threading import RLock
from types import MethodType

from _thread import start_new_thread
from pyload.manager.plugin_manager import literal_eval
from pyload.thread.plugin_thread import AddonThread
from pyload.utils.utils import lock


class AddonManager(object):
    """
    Manages addons, delegates and handles Events.

    Every plugin can define events, \
    but some very usefull events are called by the Core.
    Contrary to overwriting addon methods you can use event listener,
    which provides additional entry point in the control flow.
    Only do very short tasks or use threads.

    **Known Events:**
    Most addon methods exists as events. These are the additional known events.

    ===================== ============== ==================================
    Name                     Arguments      Description
    ===================== ============== ==================================
    downloadPreparing     fid            A download was just queued and will be prepared now.
    downloadStarts        fid            A plugin will immediately starts the download afterwards.
    linksAdded            links, pid     Someone just added links, you are able to modify the links.
    allDownloadsProcessed                Every link was handled, pyload would idle afterwards.
    allDownloadsFinished                 Every download in queue is finished.
    unrarFinished         folder, fname  An Unrar job finished
    configChanged                        The config was changed via the api.
    pluginConfigChanged                  The plugin config changed, due to api or internal process.
    ===================== ============== ==================================

    | Notes:
    |    allDownloadsProcessed is *always* called before allDownloadsFinished.
    |    configChanged is *always* called before pluginConfigChanged.
    """

    def __init__(self, core):
        self.pyload = core
        self.config = self.pyload.config

        builtins.ADDONMANAGER = self  #: needed to let addons register themself

        self.log = self.pyload.log
        self.plugins = []
        self.pluginMap = {}
        self.methods = {}  #: dict of names and list of methods usable by rpc

        self.events = {}  #: contains events

        # registering callback for config event
        self.config.pluginCB = MethodType(self.dispatchEvent, "pluginConfigChanged")

        self.addEvent("pluginConfigChanged", self.manageAddons)

        self.lock = RLock()
        self.createIndex()

    def try_catch(func):
        def new(*args):
            try:
                return func(*args)
            except Exception as e:
                args[0].log.error(_("Error executing addons: {}").format(str(e)))

        return new

    def addRPC(self, plugin, func, doc):
        plugin = plugin.rpartition(".")[2]
        doc = doc.strip() if doc else ""

        if plugin in self.methods:
            self.methods[plugin][func] = doc
        else:
            self.methods[plugin] = {func: doc}

    def callRPC(self, plugin, func, args, parse):
        if not args:
            args = tuple()
        if parse:
            args = tuple(literal_eval(x) for x in args)

        plugin = self.pluginMap[plugin]
        f = getattr(plugin, func)
        return f(*args)

    def createIndex(self):
        plugins = []

        active = []
        deactive = []

        for pluginname in self.pyload.pluginManager.addonPlugins:
            try:
                # addonClass = getattr(plugin, plugin.__name__)

                if self.pyload.config.getPlugin(pluginname, "activated"):
                    pluginClass = self.pyload.pluginManager.loadClass(
                        "addon", pluginname
                    )
                    if not pluginClass:
                        continue

                    plugin = pluginClass(self.pyload, self)
                    plugins.append(plugin)
                    self.pluginMap[pluginClass.__name__] = plugin
                    if plugin.isActivated():
                        active.append(pluginClass.__name__)
                else:
                    deactive.append(pluginname)

            except Exception:
                self.log.warning(_("Failed activating {}").format(pluginname))

        self.log.info(_("Activated plugins: {}").format(", ".join(sorted(active))))
        self.log.info(_("Deactivate plugins: {}").format(", ".join(sorted(deactive))))

        self.plugins = plugins

    def manageAddons(self, plugin, name, value):
        if name == "activated" and value:
            self.activateAddon(plugin)
        elif name == "activated" and not value:
            self.deactivateAddon(plugin)

    def activateAddon(self, plugin):

        # check if already loaded
        for inst in self.plugins:
            if inst.__name__ == plugin:
                return

        pluginClass = self.pyload.pluginManager.loadClass("addon", plugin)

        if not pluginClass:
            return

        self.log.debug("Plugin loaded: {}".format(plugin))

        plugin = pluginClass(self.pyload, self)
        self.plugins.append(plugin)
        self.pluginMap[pluginClass.__name__] = plugin

        # call core Ready
        start_new_thread(plugin.coreReady, tuple())

    def deactivateAddon(self, plugin):

        addon = None
        for inst in self.plugins:
            if inst.__name__ == plugin:
                addon = inst

        if not addon:
            return

        self.log.debug("Plugin unloaded: {}".format(plugin))

        addon.unload()

        # remove periodic call
        self.log.debug(
            "Removed callback {}".format(self.pyload.scheduler.removeJob(addon.cb))
        )
        self.plugins.remove(addon)
        del self.pluginMap[addon.__name__]

    @try_catch
    def coreReady(self):
        for plugin in self.plugins:
            if plugin.isActivated():
                plugin.coreReady()

        self.dispatchEvent("coreReady")

    @try_catch
    def coreExiting(self):
        for plugin in self.plugins:
            if plugin.isActivated():
                plugin.coreExiting()

        self.dispatchEvent("coreExiting")

    @lock
    def downloadPreparing(self, pyfile):
        for plugin in self.plugins:
            if plugin.isActivated():
                plugin.downloadPreparing(pyfile)

        self.dispatchEvent("downloadPreparing", pyfile)

    @lock
    def downloadFinished(self, pyfile):
        for plugin in self.plugins:
            if plugin.isActivated():
                if "downloadFinished" in plugin.__threaded__:
                    self.startThread(plugin.downloadFinished, pyfile)
                else:
                    plugin.downloadFinished(pyfile)

        self.dispatchEvent("downloadFinished", pyfile)

    @lock
    @try_catch
    def downloadFailed(self, pyfile):
        for plugin in self.plugins:
            if plugin.isActivated():
                if "downloadFailed" in plugin.__threaded__:
                    self.startThread(plugin.downloadFinished, pyfile)
                else:
                    plugin.downloadFailed(pyfile)

        self.dispatchEvent("downloadFailed", pyfile)

    @lock
    def packageFinished(self, package):
        for plugin in self.plugins:
            if plugin.isActivated():
                if "packageFinished" in plugin.__threaded__:
                    self.startThread(plugin.packageFinished, package)
                else:
                    plugin.packageFinished(package)

        self.dispatchEvent("packageFinished", package)

    @lock
    def beforeReconnecting(self, ip):
        for plugin in self.plugins:
            plugin.beforeReconnecting(ip)

        self.dispatchEvent("beforeReconnecting", ip)

    @lock
    def afterReconnecting(self, ip):
        for plugin in self.plugins:
            if plugin.isActivated():
                plugin.afterReconnecting(ip)

        self.dispatchEvent("afterReconnecting", ip)

    def startThread(self, function, *args, **kwargs):
        return AddonThread(self.pyload.threadManager, function, args, kwargs)

    def activePlugins(self):
        """
        returns all active plugins.
        """
        return [x for x in self.plugins if x.isActivated()]

    def getAllInfo(self):
        """
        returns info stored by addon plugins.
        """
        info = {}
        for name, plugin in self.pluginMap.items():
            if plugin.info:
                # copy and convert so str
                info[name] = {
                    x: str(y) if not isinstance(y, str) else y
                    for x, y in plugin.info.items()
                }
        return info

    def getInfo(self, plugin):
        info = {}
        if plugin in self.pluginMap and self.pluginMap[plugin].info:
            info = {
                x: str(y) if not isinstance(y, str) else y
                for x, y in self.pluginMap[plugin].info.items()
            }

        return info

    def addEvent(self, event, func):
        """
        Adds an event listener for event name.
        """
        if event in self.events:
            if func not in self.events[event]:
                self.events[event].append(func)
        else:
            self.events[event] = [func]

    def removeEvent(self, event, func):
        """
        removes previously added event listener.
        """
        if event in self.events:
            if func in self.events[event]:
                self.events[event].remove(func)

    def dispatchEvent(self, event, *args):
        """
        dispatches event with args.
        """
        if event in self.events:
            for f in self.events[event]:
                try:
                    f(*args)
                except Exception as e:
                    self.log.warning(
                        "Error calling event handler {}: {}, {}, {}".format(
                            event, f, args, str(e)
                        )
                    )