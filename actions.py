"""doc coming soon"""

from textwrap import TextWrapper

class Action():
        
    def __init__(
            self,
            name,
            action,
            usage = None,
            description = None,
            allowed_arguments = None,
            call_with_action_name = True
    ):
        self.name = name
        self.usage = usage
        self.description = description
        if allowed_arguments is not None:
            self.allowed_arguments = allowed_arguments
        self.call_with_action_name = call_with_action_name
        self.__action = action

    def __call__(self, *args):
        if self.call_with_action_name:
            self.__action(self.name, *args)
        else:
            self.__action(*args)

    def __repr__(self):
        return self.usage
        
    @property
    def usage(self):
        return self.__usage

    @usage.setter
    def usage(self, msg):
        self.__usage = _get_nice_usage(
            msg, msg_type = 'usage')

    @property
    def description(self):
        return self.__description

    @description.setter
    def description(self, msg):
        self.__description = _get_nice_usage(
            msg, msg_type = 'description')
        
class ActionContainer():
    
    def __init__(self, call_with_action_name = True):
        # store actions as they are added
        self.__instances = []
        self.usage = ''
        self.call_with_action_name = call_with_action_name
    
    def add_action(self, name, *args, **kwargs):
        if 'call_with_action_name' not in kwargs:
            kwargs['call_with_action_name'] = self.call_with_action_name
            
        setattr(
            self,
            name,
            Action(
                name,
                *args,
                **kwargs
            )
        )
        self.__instances.append(getattr(self, name))
        if getattr(self, name).description:
            self.usage += getattr(self, name).description + '\n'
        if getattr(self, name).usage:
            self.usage += getattr(self, name).usage + '\n'

    def __iter__(self):
        return iter(self.__instances)

    def __getitem__(self, item):
        return getattr(self, item)
    
def _get_nice_usage(msg, msg_type = None):
    if msg is None:
        return ''
    
    if msg_type == 'usage':
        i_indent = '    '
        s_indent = '        '
    elif msg_type == 'description':
        i_indent = '* '
        s_indent = '  '
    else:
        i_indent = ''
        s_indent = ''
        
    wrapper = TextWrapper(
        width = 79,
        initial_indent = i_indent,
        subsequent_indent = s_indent
    )
    
    return wrapper.fill(' '.join(msg.split()))
