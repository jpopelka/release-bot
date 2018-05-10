from contextlib import contextmanager
import getpass
from glob import glob
import os
from sys import exit
from tempfile import TemporaryDirectory

from .utils import shell_command, update_spec


class Fedora:
    def __init__(self, configuration):
        self.conf = configuration
        self.logger = configuration.logger

    @contextmanager
    def username(self):
        """Workaround for getpass.getuser() failing on Openshift due to random uid."""
        user = ''
        try:
            user = getpass.getuser()
        except KeyError:
            # if LOGNAME is set, getpass.getuser() returns its value instead of failing
            os.environ['LOGNAME'] = self.conf.fas_username

        try:
            yield
        finally:
            if not user:
                del os.environ['LOGNAME']

    def fedpkg_clone_repository(self, directory, name):
        if os.path.isdir(directory):
            if shell_command(directory,
                             f"fedpkg clone {name!r}",
                             "Cloning fedora repository failed:"):
                return os.path.join(directory, name)
            else:
                return ''
        else:
            self.logger.error(f"Cannot clone fedpkg repository into non-existent directory:")
            exit(1)

    def fedpkg_switch_branch(self, directory, branch, fail=True):
        if os.path.isdir(directory):
            return shell_command(directory,
                                 f"fedpkg switch-branch {branch}",
                                 f"Switching to {branch} failed:", fail)
        else:
            self.logger.error(f"Cannot access fedpkg repository:")
            exit(1)

    def fedpkg_build(self, directory, branch, scratch=False, fail=True):
        if os.path.isdir(directory):
            return shell_command(directory,
                                 f"fedpkg build {'--scratch' if scratch else ''}",
                                 f"Building branch {branch!r} in Fedora failed:", fail)
        else:
            self.logger.error(f"Cannot access fedpkg repository:")
            exit(1)

    def fedpkg_push(self, directory, branch, fail=True):
        if os.path.isdir(directory):
            return shell_command(directory,
                                 f"fedpkg push",
                                 f"Pushing branch {branch!r} to Fedora failed:", fail)
        else:
            self.logger.error(f"Cannot access fedpkg repository:")
            exit(1)

    def fedpkg_merge(self, directory, branch, ff_only=True, fail=True):
        if os.path.isdir(directory):
            return shell_command(directory,
                                 f"git merge master {'--ff-only' if ff_only else ''}",
                                 f"Merging master to branch {branch!r} failed:", fail)
        else:
            self.logger.error(f"Cannot access fedpkg repository:")
            exit(1)

    def fedpkg_commit(self, directory, branch, message, fail=True):
        if os.path.isdir(directory):
            return shell_command(directory,
                                 f"fedpkg commit -m '{message}'",
                                 f"Committing on branch {branch} failed:", fail)
        else:
            self.logger.error(f"Cannot access fedpkg repository:")
            exit(1)

    def fedpkg_sources(self, directory, branch, fail=True):
        if os.path.isdir(directory):
            return shell_command(directory,
                                 "fedpkg sources",
                                 f"Retrieving sources for branch {branch} failed:", fail)
        else:
            self.logger.error(f"Cannot access fedpkg repository:")
            exit(1)

    def fedpkg_spectool(self, directory, branch, fail=True):
        if os.path.isdir(directory):
            spec_files = glob(os.path.join(directory, "*spec"))
            spec_files = " ".join(spec_files)
            return shell_command(directory,
                                 f"spectool -g {spec_files}",
                                 f"Retrieving new sources for branch {branch} failed:", fail)
        else:
            self.logger.error(f"Cannot access fedpkg repository:")
            exit(1)

    def fedpkg_lint(self, directory, branch, fail=True):
        if os.path.isdir(directory):
            return shell_command(directory,
                                 "fedpkg lint",
                                 f"Spec lint on branch {branch} failed:", fail)
        else:
            self.logger.error(f"Cannot access fedpkg repository:")
            exit(1)

    def fedpkg_new_sources(self, directory, branch, sources="", fail=True):
        if os.path.isdir(directory):
            return shell_command(directory,
                                 f"fedpkg new-sources {sources}",
                                 f"Adding new sources on branch {branch} failed:", fail)
        else:
            self.logger.error(f"Cannot access fedpkg repository:")
            exit(1)

    @staticmethod
    def init_ticket(keytab, fas_username):
        if not fas_username:
            return False
        if keytab and os.path.isfile(keytab):
            cmd = f"kinit {fas_username}@FEDORAPROJECT.ORG -k -t {keytab}"
        else:
            # there is no keytab, but user still migh have active ticket - try to renew it
            cmd = f"kinit -R {fas_username}@FEDORAPROJECT.ORG"
        return shell_command(os.getcwd(), cmd, "Failed to init kerberos ticket:", False)

    def update_package(self, fedpkg_root, branch, new_release):
        """
        Pulls in new source, patches spec file, commits,
        pushes and builds new version on specified branch

        :param fedpkg_root: The root of dist-git repository
        :param branch: What Fedora branch is this
        :param new_release: an array containing info about new release, see main() for definition
        :return: True on success, False on failure
        """
        fail = branch.lower() == "master"

        # retrieve sources
        if not self.fedpkg_sources(fedpkg_root, branch, fail):
            return False

        # update spec file
        spec_path = os.path.join(fedpkg_root, f"{self.conf.repository_name}.spec")
        update_spec(spec_path, new_release)

        # check if spec file is valid
        if not self.fedpkg_lint(fedpkg_root, branch, fail):
            return False

        dir_listing = os.listdir(fedpkg_root)

        # get new source
        if not self.fedpkg_spectool(fedpkg_root, branch, fail):
            return False

        # find new sources
        dir_new_listing = os.listdir(fedpkg_root)
        sources = ""
        for item in dir_new_listing:
            if item not in dir_listing:
                # this is a new file therefore it should be added to sources
                sources += f"{item!r} "

        # if there are no new sources, abort update
        if not sources.strip():
            self.logger.warning(
                "There are no new sources, won't continue releasing to fedora")
            return False

        # add new sources
        if not self.fedpkg_new_sources(fedpkg_root, branch, sources, fail):
            return False

        # commit this change, push it and start a build
        if not self.fedpkg_commit(fedpkg_root, branch, f"Update to {new_release['version']}", fail):
            return False
        if not self.fedpkg_push(fedpkg_root, branch, fail):
            return False
        if not self.fedpkg_build(fedpkg_root, branch, False, fail):
            return False
        return True

    def release(self, new_release):
        """
        Release project in Fedora

        :param new_release: an array containing info about new release, see main() for definition
        :return: True on successful release, False on unsuccessful
        """
        status = self.init_ticket(self.conf.keytab, self.conf.fas_username)
        if not status:
            self.logger.warning(
                f"Can't obtain a valid kerberos ticket, skipping fedora release")
            return False

        with TemporaryDirectory() as tmp:
            with self.username():
                # clone the repository from dist-git
                fedpkg_root = self.fedpkg_clone_repository(tmp, self.conf.repository_name)
                if not fedpkg_root:
                    return False

                # make sure the current branch is master
                if not self.fedpkg_switch_branch(fedpkg_root, "master"):
                    return False

                # update package
                if not self.update_package(fedpkg_root, "master", new_release):
                    return False

                # cycle through other branches and merge the changes there,
                # or do them from scratch, push, build
                for branch in new_release['fedora_branches']:
                    if not self.fedpkg_switch_branch(fedpkg_root, branch, fail=False):
                        continue
                    if not self.fedpkg_merge(fedpkg_root, branch, True, False):
                        self.logger.debug(
                            f"Trying to make the changes on branch {branch!r} from scratch")
                        self.update_package(fedpkg_root, branch, new_release)
                        continue
                    if not self.fedpkg_push(fedpkg_root, branch, False):
                        continue
                    self.fedpkg_build(fedpkg_root, branch, False, False)

                    # TODO: bodhi updates submission

        return True
