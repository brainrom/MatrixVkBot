#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# A simple chat client for matrix.
# This sample will allow you to connect to a room, and send/recieve messages.
# Args: host:port username password room
# Error Codes:
# 1 - Unknown problem has occured
# 2 - Could not find the server.
# 3 - Bad URL Format.
# 4 - Bad username/password.
# 11 - Wrong room format.
# 12 - Couldn't find room.

import sys
import logging
import time
import datetime
import json
import os
import pickle
import re
import threading
import requests
import traceback
import vk
import ujson
import wget
from PIL import Image

from matrix_client.client import MatrixClient
from matrix_client.api import MatrixRequestError
from requests.exceptions import MissingSchema
import config as conf

client = None
log = None
data={}
lock = None

vk_threads = {}

vk_dialogs = {}

VK_API_VERSION = '3.0'
VK_POLLING_VERSION = '3.0'

currentchat = {}

link = 'https://oauth.vk.com/authorize?client_id={}&' \
       'display=page&redirect_uri=https://oauth.vk.com/blank.html&scope=friends,messages,offline,docs,photos,video' \
       '&response_type=token&v={}'.format(conf.vk_app_id, VK_API_VERSION)


def process_command(user,room,cmd):
  global client
  global log
  global data
  answer=None
  session_data_room=None
  session_data_vk=None
  session_data_user=None

  if re.search('^@%s:.*'%conf.username, user.lower()) is not None:
    # отправленное нами же сообщение - пропускаем:
    log.debug("skip our message")
    return True

  if user not in data["users"]:
    data["users"][user]={}
    data["users"][user]["rooms"]={}
    data["users"][user]["vk"]={}
  if room not in data["users"][user]["rooms"]:
    data["users"][user]["rooms"][room]={}
    data["users"][user]["rooms"][room]["state"]="listen_command"

  session_data_room=data["users"][user]["rooms"][room]
  session_data_vk=data["users"][user]["vk"]
  session_data_user=data["users"][user]

  cur_state=data["users"][user]["rooms"][room]["state"]

  log.debug("user=%s send command=%s"%(user,cmd))
  log.debug("cur_state=%s"%cur_state)

  # в любом состоянии отмена - всё отменяет:
  if re.search('^!stop$', cmd.lower()) is not None or \
      re.search('^!стоп$', cmd.lower()) is not None or \
      re.search('^!отмена$', cmd.lower()) is not None or \
      re.search('^!cancel$', cmd.lower()) is not None:
    data["users"][user]["rooms"][room]["state"]="listen_command"
    send_message(room,'Отменил текущий режим (%s) и перешёл в начальный режим ожидания команд. Жду команд.'%session_data_room["state"])
    return True
  elif re.search('^!стат$', cmd.lower()) is not None or \
      re.search('^!состояние$', cmd.lower()) is not None or \
      re.search('^!чат$', cmd.lower()) is not None or \
      re.search('^!chat$', cmd.lower()) is not None or \
      re.search('^!room$', cmd.lower()) is not None or \
      re.search('^!stat$', cmd.lower()) is not None:
    send_message(room,"Текущее состояние: %s"%session_data_room["state"])
    if session_data_room["state"]=="dialog":
      send_message(room,'Текущая комната: "%s"'%session_data_room["cur_dialog"]["title"])
    return True

  if cur_state == "listen_command":
    if re.search('^!*\?$', cmd.lower()) is not None or \
      re.search('^!*h$', cmd.lower()) is not None or \
      re.search('^!*помощь', cmd.lower()) is not None or \
      re.search('^!*справка', cmd.lower()) is not None or \
      re.search('^!*help', cmd.lower()) is not None:
      answer="""!login - авторизоваться в ВК
!logout - выйти из ВК
!search - поиск диалогов в ВК
      """ 
      return send_message(room,answer)

    # login
    elif re.search('^!login$', cmd.lower()) is not None:
      return login_command(user,room,cmd)
    # dialogs
    elif re.search('^!dialogs$', cmd.lower()) is not None or \
      re.search('^!диалоги$', cmd.lower()) is not None or \
      re.search('^!чаты$', cmd.lower()) is not None or \
      re.search('^!комнаты$', cmd.lower()) is not None or \
      re.search('^!chats$', cmd.lower()) is not None or \
      re.search('^!rooms$', cmd.lower()) is not None or \
      re.search('^!d$', cmd.lower()) is not None:
      return dialogs_command(user,room,cmd)

  elif cur_state == "wait_vk_id":
    # парсинг ссылки
    m = re.search('https://oauth\.vk\.com/blank\.html#access_token=[a-z0-9]*&expires_in=[0-9]*&user_id=[0-9]*',cmd)
    if m:
      code = extract_unique_code(m.group(0))
      try:
        vk_user = verifycode(code)
      except:
        send_message(room, 'Неверная ссылка, попробуйте ещё раз!')
        log.warning("error auth url from user=%s"%user)
        return False
      send_message(room,'Вход выполнен в аккаунт {} {}!'.format(vk_user['first_name'], vk_user['last_name']))
      data["users"][user]["vk"]["vk_id"]=code
      data["users"][user]["rooms"][room]["state"]="listen_command"
      # сохраняем на диск:
      save_data(data)

  elif cur_state == "wait_dialog_index":
    try:
      index=int(cmd)
    except:
      send_message(room,"пожалуйста, введите номер диалога или команды !stop, !отмена, !cancel")
      return True
    if index not in session_data_room["dialogs_list"]:
      send_message(room,"Неверный номер диалога, введите верный номер диалога или команды !stop, !отмена, !cancel")
      return True
    cur_dialog=session_data_room["dialogs_list"][index]
    send_message(room,"Переключаю Вас на диалог с: %s"%cur_dialog["title"])
    data["users"][user]["rooms"][room]["cur_dialog"]=cur_dialog
    data["users"][user]["rooms"][room]["state"]="dialog"
    # сохраняем на диск:
    save_data(data)

  elif cur_state == "dialog":
    dialog=session_data_room["cur_dialog"]
    if vk_send_text(session_data_vk["vk_id"],dialog["id"],cmd,dialog["group"]) == False:
      log.error("error vk_send_text() for user %s"%user)
      send_message(room,"/me не смог отправить сообщение в ВК - ошибка АПИ")

  return True

def get_new_vk_messages(user):
  global data
  global lock
  if "vk" not in data["users"][user]:
    return False
  if "vk_id" not in data["users"][user]["vk"]:
    return False
  session = get_session(data["users"][user]["vk"]["vk_id"])
  # метки времени у пользователя ещё не выставлены:
  if "ts" not in data["users"][user]["vk"] or "pts" not in data["users"][user]["vk"]:
    # выставляем текущие метки:
    with lock:
      data["users"][user]["vk"]["ts"], data["users"][user]["vk"]["pts"] = get_tses(session)
  
  log.debug("ts=%d, pts=%d"%(data["users"][user]["vk"]["ts"], data["users"][user]["vk"]["pts"]))

  api = vk.API(session, v=VK_POLLING_VERSION)
  try:
    ts_pts = ujson.dumps({"ts": data["users"][user]["vk"]["ts"], "pts": data["users"][user]["vk"]["pts"]})
    new = api.execute(code='return API.messages.getLongPollHistory({});'.format(ts_pts))
  except vk.api.VkAPIError:
    timeout = 3
    log.warning('Retrying getLongPollHistory in {} seconds'.format(timeout))
    time.sleep(timeout)
    with lock:
      data["users"][user]["vk"]["ts"], data["users"][user]["vk"]["pts"] = get_tses(session)
    ts_pts = ujson.dumps({"ts": data["users"][user]["vk"]["ts"], "pts": data["users"][user]["vk"]["pts"]})
    new = api.execute(code='return API.messages.getLongPollHistory({});'.format(ts_pts))

  msgs = new['messages']
  with lock:
    data["users"][user]["vk"]["pts"] = new["new_pts"]
  count = msgs[0]

  res = []
  if count == 0:
      pass
  else:
      res = msgs[1:]
  return res


def extract_unique_code(text):
    # Extracts the unique_code from the sent /start command.
    try:
        return text[45:].split('&')[0]
    except:
        return None

def get_session(token):
    return vk.Session(access_token=token)

def get_tses(session):
    api = vk.API(session, v=VK_POLLING_VERSION)
    ts = api.messages.getLongPollServer(need_pts=1)
    return ts['ts'], ts['pts']

def verifycode(code):
    session = vk.Session(access_token=code)
    api = vk.API(session, v=VK_API_VERSION)
    return dict(api.account.getProfileInfo(fields=[]))


def info_extractor(info):
    info = info[-1].url[8:-1].split('.')
    return info

def vk_send_text(vk_id, chat_id, message, group=False, forward_messages=None):
  global log
  try:
    session = get_session(vk_id)
    api = vk.API(session, v=VK_API_VERSION)
    if group:
      api.messages.send(chat_id=chat_id, message=message, forward_messages=forward_messages)
    else:
      api.messages.send(user_id=chat_id, message=message, forward_messages=forward_messages)
  except:
    log.error("vk_send_text API or network error")
    return False
  return True

def dialogs_command(user,room,cmd):
  global log
  global lock
  global data
  log.debug("dialogs_command()")
  session_data_room=data["users"][user]["rooms"][room]
  session_data_vk=data["users"][user]["vk"]
  if "vk_id" not in session_data_vk or session_data_vk["vk_id"]==None:
    send_message(room,'Вы не вошли в ВК - используйте !login для входа')
    return True
  vk_id=session_data_vk["vk_id"]
  dialogs=get_dialogs(vk_id)
  if dialogs == None:
    send_message(room,'Не смог получить спиоок бесед из ВК - попробуйте позже :-(')
    log.error("get_dialogs() for user=%s"%user)
    return False

  # Формируем список диалогов:
  send_message(room,"Выберите диалог:")
  message=""
  index=1
  dialogs_list={}
  for item in dialogs:
    dialogs_list[index]=item
    message+="%d. "%index
    message+=item["title"]
    message+="\n"
    index+=1
  send_message(room,message)
  data["users"][user]["rooms"][room]["state"]="wait_dialog_index"
  data["users"][user]["rooms"][room]["dialogs_list"]=dialogs_list
  return True

def get_dialogs(vk_id):
  global log
  # Формируем структуры:
  order = []
  users_ids = []
  group_ids = []
  positive_group_ids = []
  try:
    api = vk.API(get_session(vk_id), v=VK_API_VERSION)
    dialogs = api.messages.getDialogs(count=200)
  except:
    log.error("get dialogs from VK API")
    return None
  for chat in dialogs[1:]:
    if 'chat_id' in chat:
      chat['title'] = replace_shields(chat['title'])
      order.append({'title': chat['title'], 'id': chat['chat_id'], 'group': True})
    elif chat['uid'] > 0:
      order.append({'title': None, 'id': chat['uid'], 'group': False})
      users_ids.append(chat['uid'])
    elif chat['uid'] < 0:
      order.append({'title': None, 'id': chat['uid'],'group': False})
      group_ids.append(chat['uid'])

  for g in group_ids:
    positive_group_ids.append(str(g)[1:])

  if users_ids:
    users = api.users.get(user_ids=users_ids, fields=['first_name', 'last_name', 'uid'])
  else:
    users = []

  if positive_group_ids:
    groups = api.groups.getById(group_ids=positive_group_ids, fields=[])
  else:
    groups = []

  for output in order:
    if output['title'] == ' ... ' or not output['title']:
      if output['id'] > 0:
        for x in users:
          if x['uid'] == output['id']:
            output['title'] = '{} {}'.format(x['first_name'], x['last_name'])
            break
      else:
        for f in groups:
          if str(f['gid']) == str(output['id'])[1:]:
            output['title'] = '{}'.format(f['name'])
            break
  return order

def login_command(user,room,cmd):
  global lock
  global data
  log.debug("login_command()")
  session_data_vk=data["users"][user]["vk"]
  if "vk_id" not in session_data_vk or session_data_vk["vk_id"]==None:
    send_message(room,'Нажмите по ссылке ниже. Откройте её и согласитесь. После скопируйте текст из адресной строки и отправьте эту ссылку мне сюда')
    send_message(room,link)
    data["users"][user]["rooms"][room]["state"]="wait_vk_id"
  else:
    send_message(room,'Вход уже выполнен!\n/logout для выхода.')

def replace_shields(text):
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&amp;', '&')
    text = text.replace('&copy;', '©')
    text = text.replace('&reg;', '®')
    text = text.replace('&laquo;', '«')
    text = text.replace('&raquo;', '«')
    text = text.replace('&deg;', '°')
    text = text.replace('&trade;', '™')
    text = text.replace('&plusmn;', '±')
    return text














def save_data(data):
  global log
  log.debug("save to data_file:%s"%conf.data_file)
  try:
    data_file=open(conf.data_file,"wb")
  except:
    log.error("open(%s) for writing"%conf.data_file)
    return False
    
  try:
    pickle.dump(data,data_file)
    data_file.close()
  except:
    log.error("pickle.dump to '%s'"%conf.data_file)
    return False
  return True

def load_data():
  global log
  tmp_data_file=conf.data_file
  reset=False
  if os.path.exists(tmp_data_file):
    log.debug("Загружаем файл промежуточных данных: '%s'" % tmp_data_file)
    data_file = open(tmp_data_file,'rb')
    try:
      data=pickle.load(data_file)
      data_file.close()
      log.debug("Загрузили файл промежуточных данных: '%s'" % tmp_data_file)
    except:
      log.warning("Битый файл сессии - сброс")
      reset=True
    if not "users" in data:
      log.warning("Битый файл сессии - сброс")
      reset=True
  else:
    log.warning("Файл промежуточных данных не существует")
    reset=True
  if reset:
    log.warning("Сброс промежуточных данных")
    data={}
    data["users"]={}
    save_data(data)
  return data


def send_html(room_id,html):
  global client
  global log

  room=None
  try:
    room = client.join_room(room_id)
  except MatrixRequestError as e:
    print(e)
    if e.code == 400:
      log.error("Room ID/Alias in the wrong format")
      return False
    else:
      log.error("Couldn't find room.")
      return False
  try:
    room.send_html(html)
  except:
    log.error("Unknown error at send message '%s' to room '%s'"%(html,room_id))
    return False
  return True

def send_message(room_id,message):
  global client
  global log

  #FIXME отладка парсера
  #print("message=%s"%message)
  #return True

  room=None
  try:
    room = client.join_room(room_id)
  except MatrixRequestError as e:
    print(e)
    if e.code == 400:
      log.error("Room ID/Alias in the wrong format")
      return False
    else:
      log.error("Couldn't find room.")
      return False
  try:
    room.send_text(message)
  except:
    log.error("Unknown error at send message '%s' to room '%s'"%(message,room_id))
    return False
  return True

# Called when a message is recieved.
def on_message(event):
    global client
    global log
    global lock
    print(json.dumps(event, indent=4, sort_keys=True,ensure_ascii=False))
    if event['type'] == "m.room.member":
        if event['membership'] == "join":
            print("{0} joined".format(event['content']['displayname']))
    elif event['type'] == "m.room.message":
        if event['content']['msgtype'] == "m.text":
            print("{0}: {1}".format(event['sender'], event['content']['body']))
            log.debug("try lock before process_command()")
            with lock:
              log.debug("success lock before process_command()")
              if process_command(event['sender'], event['room_id'],event['content']['body']) == False:
                log.error("error process command: '%s'"%event['content']['body'])
                return False
    else:
      print(event['type'])
    return True

def on_event(event):
    print("event:")
    print(event)
    print(json.dumps(event, indent=4, sort_keys=True,ensure_ascii=False))

def on_invite(room, event):
    global client
    global log

    if conf.debug:
      print("invite:")
      print("room_data:")
      print(room)
      print("event_data:")
      print(event)
      print(json.dumps(event, indent=4, sort_keys=True,ensure_ascii=False))

    # Просматриваем сообщения:
    for event_item in event['events']:
      if event_item['type'] == "m.room.join_rules":
        if event_item['content']['join_rule'] == "invite":
          # Приглашение вступить в комнату:
          room = client.join_room(room)
          room.send_text("Спасибо за приглашение! Недеюсь быть Вам полезным. :-)")
          room.send_text("Для справки по доступным командам - неберите: '!help' (или '!?', или '!h')")
          log.info("New user: '%s'"%event_item["sender"])

def exception_handler(e):
  global client
  global log
  log.error("main listener thread except. He must retrying...")
  print(e)
  log.info("wait 30 second before retrying...")
  time.sleep(30)

def main():
    global client
    global data
    global log
    global lock

    lock = threading.RLock()

    log.debug("try lock before main load_data()")
    with lock:
      log.debug("success lock before main load_data()")
      data=load_data()

    log.info("try init matrix-client")
    client = MatrixClient(conf.server)
    log.info("success init matrix-client")

    try:
        log.info("try login matrix-client")
        client.login_with_password(username=conf.username, password=conf.password)
        log.info("success login matrix-client")
    except MatrixRequestError as e:
        print(e)
        log.debug(e)
        if e.code == 403:
            log.error("Bad username or password.")
            sys.exit(4)
        else:
            log.error("Check your sever details are correct.")
            sys.exit(2)
    except MissingSchema as e:
        log.error("Bad URL format.")
        print(e)
        log.debug(e)
        sys.exit(3)

    log.info("try init listeners")
    client.add_listener(on_message)
    client.add_ephemeral_listener(on_event)
    client.add_invite_listener(on_invite)
    client.start_listener_thread(exception_handler=exception_handler)
    log.info("success init listeners")

    x=0
    log.info("enter main loop")
    while True:
      print("step %d"%x)
      for user in data["users"]:
        for room in data["users"][user]:
          res=get_new_vk_messages(user)
          for m in res:
            print("m:")
            print(m)
            for room in data["users"][user]["rooms"]:
              if "cur_dialog" in data["users"][user]["rooms"][room]:
                print("cur_dialog:")
                print(data["users"][user]["rooms"][room]["cur_dialog"])
                if data["users"][user]["rooms"][room]["cur_dialog"]["id"] == m["uid"]:
                  send_message(room,m["body"])
          print("res:")
          print(res)
          if res == False:
            print("data:")
            print(data)
      x+=1
      time.sleep(3)
    log.info("exit main loop")


if __name__ == '__main__':
  log= logging.getLogger("MatrixVkBot")
  if conf.debug:
    log.setLevel(logging.DEBUG)
  else:
    log.setLevel(logging.INFO)

  # create the logging file handler
  fh = logging.FileHandler(conf.log_path)
  formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
  fh.setFormatter(formatter)

  if conf.debug:
    # логирование в консоль:
    #stdout = logging.FileHandler("/dev/stdout")
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(formatter)
    log.addHandler(stdout)

  # add handler to logger object
  log.addHandler(fh)

  log.info("Program started")
  main()
  log.info("Program exit!")
