# -*- coding: utf-8 -*-
import re
import time
import types
import json
import traceback
from slackclient import SlackClient
import requests
import gitlab

BULLET = u'\u2022'

DRONE_BUILD_INFO_FORMAT = u'''
Number: {number}
Commit: {commit}
Author: {author_email}
Ref: {branch}
Event: {event}

{message}
'''.rstrip()

DRONE_BUILD_HELP_DESC = u'''
Drone CI build helper

Options:
* `repo`: repo name eg;`sulee/cleffa`
* `id`: target build number
* `r`: request rebuild

Options example:
* `sulee/cleffa`
* `sulee/cleffa 1`
* `sulee/cleffa 1 r`
'''.strip()


class GitRepo(object):
    def commit_info(self, ref):
        pass

    def update_tag(self, name, ref):
        pass


class GitlabRepo(GitRepo):
    def __init__(self, url, token, repo):
        self.gl = gitlab.Gitlab(url, token)
        self.project = self.gl.projects.get(repo)

    def commit_info(self, ref):
        # TODO need about who submitted?
        commit = self.project.commits.get(ref)
        return dict(sha=commit.id,
                    message=commit.message,
                    author=u'%s <%s>' % (commit.author_name, commit.author_email))

    def update_tag(self, name, ref):
        try:
            self.project.tags.delete(name)
        except gitlab.exceptions.GitlabDeleteError:
            pass
        self.project.tags.create(dict(tag_name=name, ref=ref))


class Drone(object):
    def __init__(self, host, token):
        self.host = host
        self.token = token
        self.session = requests.session()
        self.session.headers['Authorization'] = 'Bearer ' + self.token

    def get(self, url):
        full_url = self.host + url
        try:
            return json.loads(self.session.get(full_url).text)
        except ValueError:
            return dict(error='unknown error')

    def post(self, url, data=None):
        full_url = self.host + url
        try:
            return json.loads(self.session.post(full_url, data=data).text)
        except ValueError:
            return dict(error='unknown error')


class SlackChannel(object):
    def __init__(self, nickname, token):
        self.client = SlackClient(token)
        self.nickname = nickname
        self.handles = []
        self.uptime = time.time()

    def connect(self):
        return self.client.rtm_connect()

    def bind_bot_info(self):
        users = self.client.api_call('users.list')
        if not users.get('ok'):
            self.userid = 'unknown'
        else:
            for user in users.get('members'):
                if 'name' in user and user.get('name') == self.nickname:
                    self.userid = user.get('id')
                    break
        self.call_signs = [self.nickname, '<@' + self.userid + '>']
        #print self.call_signs

    def mainloop(self):
        if not self.connect():
            # TODO throw
            return False
        self.bind_bot_info()
        while True:
            for chan, sender, payload, raw in self.parse_data(self.client.rtm_read()):
                if not chan or not payload:
                    continue
                try:
                    self.do_handles(chan, sender, payload, raw)
                except:
                    slack.send(chan, sender,
                               'something got error. please contact maintainer')
                    traceback.print_exc()
            time.sleep(0.1)

    def parse_data(self, datas):
        if not datas or not len(datas):
            raise StopIteration
        for data in datas:
            if not data.get('type'):
                continue
            if data['type'] == 'hello':
                print 'bot started'
                continue
            # only allow message data
            if data['type'] != 'message':
                continue
            if not data or not data.get('text') or not data.get('user'):
                continue
            # ignore own message
            if data['user'] == self.userid:
                continue
            # ignore old request message
            if data['ts'] and float(data['ts']) < self.uptime:
                continue
            payload = filter(lambda x: x,
                             map(lambda x: x.strip(),
                                 data['text'].split(' ')))
            if payload[0] not in self.call_signs:
                continue
            yield data['channel'], data['user'], payload[1:], data

    def do_handles(self, chan, sender, payload, raw):
        for handle in self.handles:
            handle(self, chan, sender, payload, raw)

    def add_handle(self, handle):
        self.handles.append(handle)

    def find_handle(self, name):
        for handle in self.handles:
            if name == handle.name:
                return handle

    def send(self, channel, sender, message):
        slack.client.rtm_send_message(channel=channel,
                                      message=u'<@%s> %s' % (sender, message))


class Command(object):
    def __init__(self, name, length=1, over=False):
        self.name = name
        self.arg_length = length
        self.allow_length_over = over
        self.arg_info = ''
        self.description = ''

    def __call__(self, slack, chan, sender, payload, raw):
        pass

    def valid_payload(self, payload):
        if not self.allow_length_over:
            if len(payload) != self.arg_length:
                return False
        else:
            if len(payload) < self.arg_length:
                return False
        if payload[0] != self.name:
            return False
        return True

    def detail_help_messages(self, nick):
        return u'\nUsing: `{nick} {name} {args}`\n\n{desc}' \
                .format(nick=nick, name=self.name, args=self.arg_info,
                        desc=self.description) \
                .rstrip()


class PingCommand(Command):
    def __init__(self):
        # ping
        super(PingCommand, self).__init__('ping', 1)
        self.description = 'Check bot alive'

    def __call__(self, slack, chan, sender, payload, raw):
        if not self.valid_payload(payload):
            return
        append = ''
        if raw.get('ts'):
            delay = int((time.time() - float(raw['ts'])) * 1000)
            append = ' ({delay}ms)'.format(delay=delay)
        slack.send(chan, sender, 'pong' + append)


class TagCommand(Command):
    def __init__(self):
        super(TagCommand, self).__init__('tag', 4)
        self.arg_info = '<repo> <tag_name> <ref>'
        self.description = 'Create or update git repo tag'

    def __call__(self, slack, chan, sender, payload, raw):
        if not self.valid_payload(payload):
            return
        try:
            git = GitlabRepo(config['GITLAB']['HOST'], config['GITLAB']['TOKEN'],
                             payload[1])
        except gitlab.exceptions.GitlabGetError:
            slack.send(chan, sender, 'unknown repo')
            return
        try:
            # update_tag(name, ref)
            git.update_tag(payload[2], payload[3])
        except:
            slack.send(chan, sender, 'unknown error from server')
            return
        slack.send(chan, sender, 'create tag %s as %s' % (payload[2], payload[3]))


class CommitInfoCommand(Command):
    def __init__(self):
        # commit repo ref
        super(CommitInfoCommand, self).__init__('commit', 3)
        self.arg_info = '<repo> <ref>'
        self.description = 'Return commit reference information.'

    def __call__(self, slack, chan, sender, payload, raw):
        if not self.valid_payload(payload):
            return
        try:
            git = GitlabRepo(config['GITLAB']['HOST'], config['GITLAB']['TOKEN'],
                             payload[1])
        except gitlab.exceptions.GitlabGetError:
            slack.send(chan, sender, 'unknown repo')
            return
        try:
            info = git.commit_info(payload[2])
        except:
            slack.send(chan, sender, 'unknown ref')
            return
        message = u'\nCommit: {sha}\nAuthor: {author}\n\n{message}'.format(**info)
        slack.send(chan, sender, message)


class BuildCommand(Command):
    def __init__(self):
        # commit repo ref
        super(BuildCommand, self).__init__('build', 2, over=True)
        self.arg_info = '<repo> [id [r]]'
        self.description = DRONE_BUILD_HELP_DESC

    def __call__(self, slack, chan, sender, payload, raw):
        if not self.valid_payload(payload):
            return
        drone = Drone(config['DRONE']['HOST'], config['DRONE']['TOKEN'])
        payload_len = len(payload)
        build_info = DRONE_BUILD_INFO_FORMAT
        if payload_len == 2:
            data = drone.get('/api/repos/%s/builds' % payload[1])
            if type(data) == types.DictType and data.get('error'):
                message = data['error']
            else:
                message = u'Latest build\n' + build_info.format(**data[0])
        elif payload_len == 3:
            data = drone.get('/api/repos/%s/builds/%s' % tuple(payload[1:3]))
            if type(data) == types.DictType and data.get('error'):
                message = data['error']
            else:
                message = build_info.format(**data)
        elif payload_len == 4:
            if payload[3] == 'r':
                data = drone.post('/api/repos/%s/builds/%s' % tuple(payload[1:3]))
                if type(data) == types.DictType and data.get('error'):
                    message = data['error']
                else:
                    message = u'Job enqueued\n' + build_info.format(**data)
            else:
                message = 'unknown subcommand'
        slack.send(chan, sender, message)


class HelpCommand(Command):
    def __init__(self):
        # help <command>
        super(HelpCommand, self).__init__('help', 1, over=True)
        self.arg_info = '[command]'
        self.description = '$ man man'

    def __call__(self, slack, chan, sender, payload, raw):
        if not self.valid_payload(payload):
            return
        if len(payload) > 1:
            handle = slack.find_handle(payload[1])
            if not handle:
                return slack.sand(chan, sender, 'unknown command')
            slack.send(chan, sender, handle.detail_help_messages(slack.nickname))
        else:
            names = sorted(handle.name for handle in slack.handles)

            message = u'All command list\n\n{commands}' \
                    .format(commands='\n'.join(BULLET + ' `' + n + '`' for n in names))
            slack.send(chan, sender, message)


if __name__ == '__main__':
    import yaml

    config = yaml.safe_load(open('config.yml'))

    slack = SlackChannel(config['SLACK']['NAME'], config['SLACK']['TOKEN'])

    slack.add_handle(TagCommand())
    slack.add_handle(CommitInfoCommand())
    slack.add_handle(PingCommand())
    slack.add_handle(BuildCommand())
    slack.add_handle(HelpCommand())

    slack.mainloop()
