###############################################################################
#
# Copyright (C) 2015 Aleksandrina Nikolova <aayla.secura.1138@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################
#
# A wrapper class for Serial and DaemonContext,
# Requires pyserial, python-daemon and regex

"""Serial port communication daemon with inet socket interface.

Exported classes:
SerialDaemon: the daemon listening on port Y communicating with ttySX.
"""

import lockfile
import os
import regex
import signal
import socket
import sys
from syslog import openlog as opensyslog
from syslog import closelog as closesyslog
from syslog import syslog as logsyslog
from syslog import LOG_INFO, LOG_DAEMON, LOG_ERR
import traceback
import tempfile
from time import sleep
from daemon import DaemonContext
from serial import Serial

class SerialDaemon():
    """A wrapper class for Serial and DaemonContext with inet socket support.

    Creates a DaemonContext object and a Serial object using the passed
    arguments. Some arguments do not take effect after the daemon is started
    and hence can be altered anytime after initialization until it is started
    by calling <daemon>.start()

    Communication with the daemon is done on a port number specified during
    initialization (or anytime before start), defaulting to 57001. Data is
    decoded using the user specified encoding (default is UTF-8) and must be
    in the following format:
    <0-F><0-F0-F...0-F><data to be sent to device>
    where the first byte of data always signifies how many (base 16) bytes
    following it give the the number (base 16) of bytes that are to be read
    from device after the data is sent to it. 0 bytes are read if data[0] is
    0 or is not a valid hex number. <data[0] number of bytes> + 1 are always
    discarded from the beginning of data. For example:
        22F8921 sends 8921 to device and reads 2F (47) bytes of data
        X78192 sends 78192 to device and dos not read a reply
        3A2G9130 sends 9130 to device BUT does not read a reply since A2G is
            not a valid hex number. Note that these 3 bytes are still discarded

    This class does not inherit from either DaemonContext or Serial.
    The only export method is start() used to run the daemon.

    Accepted options for the constructor:

    name
        :Default: ``None``

        The name of the daemon, used for syslog and the default
        name of the pidfile and configuration files. Changes to this value
        after the daemon has started will only be reflected in syslog.

    config_file
        :Default: ``'/etc/<name>.conf'``

        Configuration file used to set some of the hereby listed parameters.
        Currently, only pidfile_path, socket_port, socket_host, data_length
        and data_encoding are supported. Reloading of the configuration without
        restarting is done by sending SIGHUP to the daemon but please note that
        changes to pidfile_path, socket_port or socket_host require restart to
        take effect.
        The format is as follows:
            <option_name> = <option value>
        Option names are case-INsensitive and ``_`` may be replaced by ``-``.
        Spaces around ``=`` are optional and have no effect.
        Option values may optionally be enclosed in either single or double
        quotes. # and any text following it on the line are ignored.

    log_file
        :Default: ``/tmp/<name>.log``

        Log file used to log exceptions during daemon run.

    pidfile_path
        :Default: ``'/var/run/<name>.pid'``

        Path to the pidfile. A pidfile lock is created and passed to
        DaemonContext. Alternatively, you may pass a pid lockfile directly by
        setting <daemon>.daemon_context.pidfile to the lockfile after
        initialization but before start. Changing either of them after the
        daemon is started requires a restart.

    socket_port
        :Default: ``57001``

        Port which the daemon will be listening on. See above for details on
        the data format. Changing this after the daemon is started requires a
        restart. Also see documentation for serial.py.

    socket_host
        :Default: ``''``

        Interface which the daemon will be listening on. Empty string signifies
        all interfaces. See above for details on the data format. Changing this
        after the daemon is started requires a restart. Also see documentation
        for serial.py.

    data_length
        :Default: ``1024``

        Number of bytes to be read from the socket. This MUST be at least the
        number of bytes that have been sent, otherwise the remainder is read
        afterwards and is confused for a new packet. See above for details on
        the data format.

    data_encoding
        :Default: ``'utf-8'``

        Valid encoding (accepted by the str.decode() and bytes() methods) for
        the data read from and sent to the socket.

    reply_length_strict
        :Default: ``False``

        If True daemon will not send data read from device unless the length
        matches the expected reply length given as part of the data sent over
        the socket. See above for details on the data format.

    daemon_context
        :Default: ``None``

        If this is not None, it must be a DaemonContext object and is used
        instead of creating a new one. All options relating to DaemoonContext
        are then ignored.

    serial_context
        :Default: ``None``

        If this is not None, it must be a Serial object and is used instead of
        creating a new one. All options relating to Serial are then ignored.

    In addition to the above arguments, SerialDaemon accepts all arguments
    valid for DaemonContext and Serial and uses them to create the
    corresponding objects (unless daemon_context or serial_context are given)
    """
    
    def __init__(
            self,
            name = 'seriald',
            config_file = 0,
            log_file = None,
            pidfile_path = 0,
            socket_port = 57001,
            socket_host = '',		# all available interfaces
            data_length = 1024,
            reply_length_strict = False,
            data_encoding = 'utf-8',
            daemon_context = None,
            serial_context = None,
            **kwargs
    ):

        self.name = name
        self.config_file = config_file
        if self.config_file == 0:
            self.config_file = '/etc/{name}.conf'.format(name = self.name)
            
        self.log_file = log_file
        # log file will be used even if user specified None
        if self.log_file is None:
            self.log_file = os.path.join(
                tempfile.gettempdir(), '{name}.log'.format(
                    name = self.name))
            
        self.pidfile_path = pidfile_path
        if self.pidfile_path == 0:
            self.pidfile_path = '/var/run/{name}.pid'.format(name = self.name)

        self.socket_port = socket_port
        self.socket_host = socket_host
        self.data_length = data_length
        self.reply_length_strict = reply_length_strict
        self.data_encoding = data_encoding
        
        self.daemon_context = daemon_context
        if self.daemon_context is None:
            self.daemon_context = DaemonContext(
                signal_map = {
                    signal.SIGHUP: self.__accept_signal,
                    signal.SIGINT: self.__accept_signal,
                    signal.SIGQUIT: self.__accept_signal,
                    signal.SIGTERM: self.__accept_signal,
                }
            )
            for attr in filter(lambda s: not s.startswith('_'),
                               dir(self.daemon_context)):
                if kwargs.get(attr) is not None:
                    setattr(self.daemon_context, attr, kwargs.get(attr))
            
        self.serial_context = serial_context
        if self.serial_context is None:
            self.serial_context = Serial()
            for attr in filter(lambda s: not s.startswith('_'),
                               dir(self.serial_context)):
                if kwargs.get(attr) is not None:
                    setattr(self.serial_context, attr, kwargs.get(attr))
                    
    def __run(self):

        with open(self.log_file, 'a') as log_file:
            try:
                logsyslog(LOG_INFO, 'Waiting for connection')
                soc, soc_addr = self.socket.accept()
                logsyslog(LOG_INFO, 'Connected to {addr}'.format(
                    addr = soc_addr))
                
                while True:
                    if soc.fileno() < 0:
                        logsyslog(LOG_INFO, 'Waiting for connection')
                        soc, soc_addr = self.socket.accept()
                        logsyslog(LOG_INFO, ('Connected to {addr}').format(
                            addr = soc_addr))

                    data = soc.recv(self.data_length).decode(
                        self.data_encoding)
                    if data == '':
                        logsyslog(LOG_INFO, 'Closing connection')
                        soc.close()
                        continue

                    logsyslog(LOG_INFO, 'Read from socket: {data}'.format(
                        data = data))

                    reply_length_byte_length = 0
                    try:
                        reply_length_byte_length = int(data[0], 16)
                        reply_length = int(
                            data[1 : reply_length_byte_length + 1], 16)
                    except ValueError:
                        reply_length = 0
                    data = data[reply_length_byte_length + 1:]

                    if not self.serial_context.isOpen():
                        # first time in the loop
                        logsyslog(LOG_INFO, 'Opening serial port')
                        self.serial_context.open()
                        
                    logsyslog(LOG_INFO, 'Sending {data}'.format(
                        data = data))
                    # discard any input or output
                    self.serial_context.flushOutput()
                    self.serial_context.flushInput()
                    self.serial_context.write(bytes(data, self.data_encoding))
                    # flush doesn't work in daemon mode
                    # close and reopen instead
                    self.serial_context.close()
                    self.serial_context.open()
                    logsyslog(LOG_INFO, ('Will read {length} bytes').format(
                        length = reply_length))
                    
                    if reply_length > 0:
                        reply = self.serial_context.read(reply_length)
                        reply_decoded = reply.decode(self.data_encoding)
                        logsyslog(LOG_INFO, 'Received {data}'.format(
                            data = reply_decoded))
                        if len(reply_decoded) == reply_length \
                           or not self.reply_length_strict:
                            try:
                                soc.sendall(reply)
                            except ConnectionResetError:
                                soc.close()
                                continue

            except:
                traceback.print_exc(file = log_file)
                
        self.__stop()
                
    def __load_config(self):
        if self.config_file is not None:
            conf = _openfile(self.config_file, 'r')
            
        if conf is not None:
            
            with conf:
                regex_pat = regex.compile(r"""\s* (?|
                    (?P<option>
                      data[-_]length |
                      data[-_]encoding |
		      socket[-_]port |
		      socket[-_]host |
                      pidfile[-_]path
		    ) \s* (?: =\s* )?
		    (?|
                      " (?P<value> [^"]+ ) " |
		      ' (?P<value> [^']+ ) ' |
		        (?P<value> [^#\r\n]+ )
		    ) )
                    """, regex.X|regex.I)
                
                line_num = 0
                for line in conf:
                    line_num += 1
                    
                    if line.startswith('#'):
                        continue
                    
                    match = regex_pat.match(line.strip())
                    if match:
                        # translate the option name to the object's attribute
                        opt = match.group('option').lower().replace('-', '_')

                        if opt.endswith(('file',
                                         'dir',
                                         'path',
                                         'host',
                                         'encoding')):
                            val = match.group('value')
                        else:
                            # value must be numeric and positive
                            val = int(match.group('value'))
                            if val <= 0:
                                logsyslog(LOG_ERR,
                                          ('{conf}: Invalid value for ' +
                                           '{option}').format(
                                               conf = self.config_file,
                                               option = opt))
                                val = getattr(self, opt)
                                
                        setattr(self, opt, val)
                        
                    else:
                        logsyslog(LOG_ERR, ('{conf}: Invalid syntax at line ' +
                                            '{line}').format(
                                                conf = self.config_file,
                                                line = line_num))
                        
            logsyslog(LOG_INFO, '{conf} loaded'.format(
                conf = self.config_file))

    def start(self):
        """
        Load config, daemonize, connect to serial port, listen on socket port
        """
        opensyslog(ident = self.name, facility = LOG_DAEMON)
        
        self.__load_config()
        if self.pidfile_path is not None:
            self.daemon_context.pidfile = lockfile.FileLock(self.pidfile_path)
        
        if os.path.exists(self.daemon_context.pidfile.path):
            if _pidfile_isbusy(self.daemon_context.pidfile):
                logsyslog(LOG_ERR, 'Already running (pidfile is locked)')
                closesyslog()
                return

        self.daemon_context.open()
        with _openfile(self.daemon_context.pidfile.path, 'w',
                      fail = self.__stop) as file:
            file.write('{pid}'.format(pid = os.getpid()))
        # opening the serial port here doesn't work
        # open it in __run instead
        # self.serial_context.open()
        logsyslog(LOG_INFO, 'Started')

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind((self.socket_host, self.socket_port))
        self.socket.listen(1)
        logsyslog(LOG_INFO, ('Listening on port {port}').format(
            port = self.socket_port))
        self.__run()
        
    def __stop(self):
        pid = _get_pid(self.daemon_context.pidfile.path)
        if pid is None:
            return

        logsyslog(LOG_INFO, 'Stopping')
        self.socket.close()
        if self.serial_context.isOpen():
            self.serial_context.close()
        self.daemon_context.close()
        os.remove(self.daemon_context.pidfile.path)
        
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            logsyslog(LOG_ERR, 'Could not stop process id {pid}'.format(
                pid = pid))
        closesyslog()
        
    def __accept_signal(self, sig, frame):
        if sig == signal.SIGHUP:
            self.__load_config()
        else:
            logsyslog(LOG_INFO, 'Caught signal {sig}'.format(sig = sig))
            self.__stop()
            
        
def _openfile(path, mode = 'r', fail = None):
    path = os.path.realpath(path)
    try:
        file = open(path, mode)
    except IOError as error:
        if repr(error).find('Permission') >= 0:
            logsyslog(LOG_ERR,
                      'Cannot {action} {path}. Permission denied.'.format(
                          action = ('write to' if 'w' in mode else 'read'),
                          path = path))
        elif repr(error).find('No such file') >= 0:
            logsyslog(LOG_ERR, 'No such file or directory: {path}'.format(
                path = path))
        else:
            logsyslog(LOG_ERR, 'Cannot {action} {path}. Unknown error.'.format(
                action = ('write to' if 'w' in mode else 'read'),
                path = path))
    else:
        return file
    
    if fail is not None:
        fail()
        sys.exit(255)
        
def _get_pid(pidfile_path):
    pidfile = _openfile(pidfile_path, 'r')
    if pidfile is not None:
        with pidfile:
            return int(pidfile.readline().strip())
    return None

def _pidfile_isbusy(pidlock):
    if not pidlock.is_locked():
        return False
    
    pid = _get_pid(pidlock.path)
    if pid is None:
        return False
    
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True
