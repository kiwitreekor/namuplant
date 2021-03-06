import re
import time
from math import ceil
from PySide2.QtCore import QObject, Slot, Signal
import requests
from bs4 import BeautifulSoup
from urllib import parse
import pyperclip
import keyboard
from . import storage
SITE_URL = 'https://namu.wiki'

# todo 디도스 체크시 간헐적으로 정상 수행됐으면서 오류 띄우는 문제


def shorten(n: int, c='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'):
    t = ''
    while True:
        n, m = divmod(n, len(c))
        t = f'{c[m]}{t}'
        if n <= m:
            break
    return t


class Requester(QObject):
    ddos_detected = Signal()
    timeout_detected = Signal()
    pin_needed = Signal(str)
    umi_made = Signal(str)
    msg_passed = Signal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.is_ddos_checked = False
        self.typed_pin = ''
        self.URL_LOGIN = f'{SITE_URL}/member/login'
        self.s = requests.Session()
        # self.login()

    def init_login(self, user, pw):
        self.s = requests.Session()
        self.s.headers.update({'user-agent': self.config.c['login']['UA']})
        r, soup = self.request_d('post', self.URL_LOGIN, data={'username': user, 'password': pw})
        if 'umi' not in self.s.cookies:
            self.typed_pin = ''
            while True:
                mail = soup.select_one('article > div > form > p > b')
                if mail:
                    self.pin_needed.emit(mail.text)
                    while not self.typed_pin:
                        time.sleep(0.3)
                    if self.typed_pin == 'deny':
                        self.msg_passed.emit('PIN 입력이 취소되었습니다.')
                        break
                    elif self.typed_pin == 'nothing':
                        self.msg_passed.emit('PIN이 입력되지 않았습니다.')
                    _, soup = self.request_d('post', f'{self.URL_LOGIN}/pin', data={'pin': self.typed_pin, 'trust': 'on'})
                    if self.is_logged_in(soup) and 'umi' in self.s.cookies:
                        self.msg_passed.emit('로그인 성공')
                        self.umi_made.emit(self.s.cookies['umi'])
                    else:
                        self.msg_passed.emit('로그인 실패')
                    break
                else:
                    error = soup.select_one('article > div > form > div > p')
                    if error:
                        self.msg_passed.emit(error.text)
                    break
        else:
            self.umi_made.emit(self.s.cookies['umi'])
            self.msg_passed.emit('로그인 성공')

    def login(self):
        self.s = requests.Session()
        self.s.headers.update({'user-agent': self.config.c['login']['UA']})
        self.s.cookies.set('umi', self.config.c['login']['UMI'], domain=f'.{SITE_URL[8:]}')
        r, soup = self.request_d('post', self.URL_LOGIN,
                                 data={'username': self.config.c['login']['ID'],
                                       'password': self.config.c['login']['PW']})
        return self.is_logged_in(soup)

    @staticmethod
    def is_logged_in(soup):
        member = soup.select('nav > ul > li > div > div > div')
        if member[1].text == 'Member':
            print(f'로그인 성공({member[0].text})')
            return True
        else:
            print('로그인 실패')
            return False

    def request_d(self, method, url, **kwargs):  # 디도스 검사 리퀘스트
        while True:
            try:
                r = self.s.request(method, url, headers={'referer': url}, cookies=self.s.cookies, timeout=5, **kwargs)
                print(r.status_code, method)
                if r.status_code == 429:  # too many requests
                    self.is_ddos_checked = False
                    self.ddos_detected.emit()
                    while not self.is_ddos_checked:
                        time.sleep(0.3)
                    continue
                else:  # 정상
                    soup = BeautifulSoup(r.text, 'html.parser')
                    if soup.title is None:
                        print('서버가 봇 작동 감지. 재시도.')
                        continue
                    print(soup.title.text)
                    return r, soup
            except requests.exceptions.Timeout:
                self.timeout_detected.emit()
                print('타임아웃 발생')

    @Slot()
    def ddos_checked(self):
        self.is_ddos_checked = True

    @Slot(str)
    def type_pin(self, t):
        self.typed_pin = t


class ReqBasic(QObject):
    label_shown = Signal(str)
    
    def __init__(self, requester):
        super().__init__()
        self.requester = requester


class ReqPost(ReqBasic):
    sig_view_diff = Signal(str, str)

    def __init__(self, requester):
        super().__init__(requester)
        self.diff_done = 'yes'
        self.after_diff = ''

    def get_text(self, doc_code):
        doc_url = f'{SITE_URL}/edit/{doc_code}'
        text = ''
        error_log = ''
        while True:
            r, soup = self.requester.request_d('get', doc_url)
            baserev = soup.find(attrs={'name': 'baserev'})['value']
            identifier = soup.find(attrs={'name': 'identifier'})['value']
            if identifier == f'm:{self.requester.config.c["login"]["ID"]}':  # 로그인 안 되어 있으면 로그인
                break
            else:
                self.requester.login()
        if baserev == '0':
            error_log = '문서가 존재하지 않습니다.'
        else:
            if self.is_over_perm(r.url, soup):
                error_log = '편집 권한이 없습니다.'
            if soup.textarea.contents:
                text = soup.textarea.contents[0]  # soup.find(attrs={'name': 'text'}).text

        return text, baserev, identifier, error_log

    def diff(self, before, after):
        if self.diff_done == 'yes' or self.diff_done == 'no':  # 이전에 yes, no 였으면 다시 비교 창 확인
            self.diff_done = 'wait'
            self.sig_view_diff.emit(before, after)
            while self.diff_done == 'wait':  # 외부에서 diff_done, after_diff 결정
                time.sleep(0.3)
            # 새로운 diff_done
            if self.diff_done == 'no':
                error_log = '편집을 적용하지 않았습니다.'
                changed = False
            elif self.diff_done == 'quit':
                error_log = '편집을 중단했습니다.'
                changed = False
            else:  # yes, group, whole
                error_log = ''
                changed = True
        else:
            changed = False
            error_log = ''
        return changed, error_log

    def post(self, doc_code, text, rev, identifier, summary=''):
        # identifier, baserev, text, error, log
        doc_url = f'{SITE_URL}/edit/{doc_code}'
        while True:
            _, soup = self.requester.request_d('post', doc_url)  # 가짜 포스트
            if self.is_captcha(soup):  # 서버 말고 편집창에 뜨는 리캡차
                self.requester.login()
            else:
                break
        token = soup.find(attrs={'name': 'token'})['value']
        # 진짜 포스트
        _, soup = self.requester.request_d(
            'post', doc_url,
            data={'identifier': identifier, 'baserev': rev, 'text': text, 'log': summary, 'token': token, 'agree': 'Y'},
            files={'file': None})
        # 오류메시지
        error_log = self.has_alert(soup)
        return error_log

    @staticmethod
    def time_doc_log():
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())

    @staticmethod
    def time_edit_log(index):
        index = str(index)
        if '_' in index:
            index = index[index.rfind('_') + 1:]
        new = shorten(int(time.time()))
        return f'{new}_{index}'

    @Slot(str, str)
    def receive_diff_done(self, done, t):
        self.diff_done = done
        self.after_diff = t

    @staticmethod
    def is_captcha(soup):
        if '"captcha":true' in soup.select("script")[1].contents[0]:
            return True  # 편집창 캡차 활성화됨
        else:
            return False

    @staticmethod
    def has_alert(soup):
        alert = soup.select('article > div > div.a.e')
        if alert:  # 편집기 오류 메시지
            return alert[0].span.text  # 경고 문구
        else:
            return ''

    @staticmethod
    def is_over_perm(url, soup):
        if 'readonly' in soup.textarea.attrs or url.startswith(f'{SITE_URL}/new_edit_request'):
            return True  # 편집 권한 없음
        else:
            return False

    @staticmethod
    def is_exist_edit(soup):  # /edit/
        if soup.small.text == '(새 문서 생성)':
            return False  # 존재하지 않는 문서
        else:
            return True

    @staticmethod
    def find_replace(edit_list):
        text, summary = '', ''
        comp, subs = [], []
        cat_p = re.compile(r'\[\[분류:.*?\]\]')
        for edit in edit_list:  # 사전 컴파일 & 분석
            if edit[1] == '문서':
                if edit[2] == '수정':
                    if edit[3] == '텍스트':
                        if edit[4] == '찾기':
                            comp.append(edit[5])
                        elif edit[4] == '바꾸기':
                            subs.append(edit[5])
                        elif edit[4] == '지우기':
                            comp.append(edit[5])
                            subs.append('')
                    elif edit[3] == '정규식':
                        if edit[4] == '찾기':
                            comp.append(re.compile(edit[5]))
                        elif edit[4] == '바꾸기':
                            subs.append(edit[5])
                        elif edit[4] == '지우기':
                            comp.append(re.compile(edit[5]))
                            subs.append('')
                    elif edit[3] == '분류:':
                        if edit[4] == '찾기':
                            comp.append(re.compile(rf'\[\[분류: ?{re.escape(edit[5])}(?P<blur>#blur)?\]\]'))
                        elif edit[4] == '바꾸기':
                            subs.append(rf'[[분류:{edit[5]}\g<blur>]]')
                        elif edit[4] == '지우기':
                            comp.append(re.compile(rf'\[\[분류: ?{re.escape(edit[5])}.*?\]\]'))
                            subs.append('')
                    elif edit[3] == '링크':
                        if edit[4] == '찾기':
                            comp.append(re.compile(
                                rf'(?P<front>\[\[(?P<w0>.*?)\|)?\[\[{re.escape(edit[5])}(?P<anc>#.*?)?(?P<bar>\|)?'
                                rf'(?(bar)(?(front)(?P<w3>.*?)|(?P<w2>.*?))|)(?P<rear>\]\].*?(?(front)\]\]))'))
                        elif edit[4] == '바꾸기':
                            if '|' in edit[5]:  # a -> a|b
                                tmp_a = edit[5][:edit[5].find('|')]
                                tmp_b = edit[5][edit[5].find('|') + 1:]
                                subs.append(rf'\g<front>[[{tmp_a}\g<anc>|{tmp_b}\g<bar>\g<w3>\g<w2>\g<rear>')
                                # [[a|b|c]]인 경우
                                comp.append(f'|{tmp_b}|')
                                subs.append('|')
                            else:  # a -> b
                                subs.append(rf'\g<front>[[{edit[5]}\g<anc>\g<bar>\g<w3>\g<w2>\g<rear>')
                                # [[a|a]]인 경우
                                comp.append(re.compile(
                                    rf'\[\[{re.escape(edit[5])}(?P<a>|#.*?)\|{re.escape(edit[5])}\]\]'))
                                subs.append(rf'[[{edit[5]}\g<a>]]')
                        elif edit[4] == '지우기':
                            comp.append(re.compile(rf'\[\[{re.escape(edit[5])}(#[^|]*?)?\]\]'))
                            subs.append(edit[5])
                            comp.append(re.compile(
                                rf'(?P<front>(?P<lc>\[\[)(?P<w0>.*?)\|)?\[\[{re.escape(edit[5])}(?P<anc>#.*?)?(?P<bar>\|)?'
                                rf'(?(bar)(?(front)(?P<w3>.*?)|(?P<w2>.*?))|)(?P<rear>\]\](?(front)(?P<rc>\]\])))'))
                            subs.append(r'\g<lc>\g<w0>\g<w2>\g<rc>')
                    elif edit[3] == '포함':
                        if edit[4] == '찾기':
                            comp.append(re.compile(rf'\[(?i:include)\({re.escape(edit[5])}(?P<after>.*?\)\])'))
                        elif edit[4] == '바꾸기':
                            subs.append(rf'[include({edit[5]}\g<after>')
                        elif edit[4] == '지우기':
                            comp.append(re.compile(rf'\[(?i:include)\({re.escape(edit[5])}.*?\)\]( +)?(\n|$)'))
                            subs.append('')
                elif edit[2] == '삽입':
                    if edit[4] == '맨 위':
                        comp.append(re.compile(r'^'))
                        subs.append(f'{edit[5]}\n')
                    elif edit[4] == '맨 아래':
                        comp.append(re.compile(r'$'))
                        subs.append(f'\n{edit[5]}')
                    elif edit[4] == '분류 앞':
                        comp.append(True)
                        subs.append(edit[5])
                    elif edit[4] == '분류 뒤':
                        comp.append(False)
                        subs.append(edit[5])
            elif edit[1] == '요약':
                summary = edit[5]
        while True:
            text = (yield text, summary)
            for i in range(len(comp)):
                if type(comp[i]) is re.Pattern:  # 정규식
                    try:
                        text = comp[i].sub(subs[i], text)
                    except re.error:
                        print('regex error')
                        continue
                    except IndexError:
                        print('edit index error')
                        continue
                elif type(comp[i]) is bool:  # 대충 분류 삽입
                    cats = cat_p.findall(text)
                    if cats:
                        if subs[i] not in cats:
                            if comp[i]:  # 앞
                                text = text.replace(cats[0], f'{subs[i]}{cats[0]}', 1)
                            else:  # 뒤
                                text = text.replace(cats[-1], f'{cats[-1]}{subs[i]}', 1)
                    else:
                        text = f'{subs[i]}\n{text}'  # 미분류시 맨 위로
                elif type(comp[i]) is str:  # 일반 텍스트
                    text = text.replace(comp[i], subs[i])

    def get_acl(self):
        pass

    @staticmethod
    def korean_consonant(text):
        share = (ord(text[0]) - 44032) // 588
        consonant = ['ㄱ', 'ㄱ', 'ㄴ', 'ㄷ', 'ㄷ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅂ', 'ㅅ', 'ㅅ', 'ㅇ', 'ㅈ', 'ㅈ', 'ㅊ',
                     'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']
        if 0 <= share <= 18:
            return consonant[share]

    @staticmethod
    def is_file_exist(soup):
        element = soup.select('article > div > a')
        if element:
            if element[0].text == '[더보기]':
                return True
        return False


class Iterate(ReqPost):
    sig_doc_remove = Signal(int)
    sig_doc_set_current = Signal(int)
    sig_doc_error = Signal(int, str)
    sig_enable_pause = Signal(bool)
    finished = Signal()

    def __init__(self, requester):
        super().__init__(requester)
        self.is_quit = False
        self.doc_list = []  # main.iterate_start
        self.edit_dict = {}
        self.index_speed = 0

    def work(self):
        edit_index = 0
        edit_row, deleted = 0, 0
        edit_log_index, upload_t, upload_s = '', '', ''
        t1 = time.time()
        total = len(self.doc_list)
        if len(self.doc_list) == 0 or len(self.edit_dict) == 0:  # 값이 없음
            self.label_shown.emit('작업을 시작할 수 없습니다. 목록을 확인해주세요.')
        else:
            doc_logger = storage.write_csv('doc_log.csv', 'a', 'doc')
            doc_logger.send(None)
            edit_logger = storage.write_csv('edit_log.csv', 'a', 'edit')
            edit_logger.send(None)
            self.label_shown.emit('작업을 시작합니다.')
            # 본작업 루프 시작
            for i in range(len(self.doc_list)):  # 0 code, 1 title, 2 etc
                self.sig_doc_set_current.emit(i - deleted)
                if self.is_quit:  # 정지 버튼 눌려있으면 중단
                    self.label_shown.emit('작업이 정지되었습니다.')
                    break
                if self.doc_list[i][0][0] == '#':  # 편집 지시자
                    if self.diff_done == 'group':  # 그룹 실행의 편집 그룹이 종료되어 초기화. 모두 실행(whole)은 초기화 안 함
                        self.diff_done = 'yes'
                    edit_index = self.doc_list[i][0][1:]  # 편집사항 순번
                    self.label_shown.emit(f'편집사항 {edit_index}번 진행 중입니다.')
                    upload_t, upload_s = '', ''
                    if self.edit_dict[edit_index][0][1] == '파일':  # 업로드 시에는 반드시 요약보다 파일이 앞에 와야 됨
                        upload_t, upload_s = self.upload_text(self.edit_dict[edit_index])  # 파일 문서 텍스트, 요약
                    else:
                        replacer = self.find_replace(self.edit_dict[edit_index])
                        replacer.send(None)
                    edit_log_index = self.time_edit_log(edit_index)
                    for row in self.edit_dict[edit_index]:
                        edit_logger.send({'index': edit_log_index, 'opt1': row[1], 'opt2': row[2], 'opt3': row[3],
                                          'opt4': row[4], 'edit': row[5]})
                elif self.doc_list[i][0][0] == '!':  # 중단자
                    self.label_shown.emit('작업이 중단되었습니다.')
                    self.sig_doc_remove.emit(i - deleted)
                    break
                else:  # 문서, 파일
                    if i > 0:  # 목록 처음이 편집 지시자가 아닌 경우만
                        label = f'( {i + 1} / {total} ) {self.doc_list[i][1]}'
                        self.label_shown.emit(label)
                        self.after_diff = ''  # 편집 비교 초기화...
                        if self.doc_list[i][0][0] == '$':  # 파일. 0번열의 0번째 문자가 $
                            post_error = self.upload(self.doc_list[i][0][1:], self.doc_list[i][1], upload_t, upload_s)
                            doc_logger.send({'code': self.doc_list[i][0], 'title': self.doc_list[i][1],
                                             'rev': f'r0', 'time': self.time_doc_log(),
                                             'index': edit_log_index, 'error': post_error})
                        else:  # 문서
                            if self.edit_dict[edit_index][0][1] == '복구':
                                post_error = self.revert(self.doc_list[i][0], self.edit_dict[edit_index],
                                                         self.doc_list[i][2])
                                post_rev = 'evert'
                            else:  # 편집
                                post_rev, post_error = self.edit(self.doc_list[i][0], self.doc_list[i][1], replacer)

                            doc_logger.send({'code': self.doc_list[i][0], 'title': self.doc_list[i][1],
                                             'rev': f'r{post_rev}', 'time': self.time_doc_log(),
                                             'index': edit_log_index, 'error': post_error})
                        if self.index_speed == 1:  # 저속 옵션
                            t2 = time.time()
                            waiting = float(self.requester.config.c['work']['DELAY']) - (t2 - t1)
                            if waiting > 0:
                                time.sleep(waiting)
                            t1 = time.time()
                        if post_error:  # 에러 발생시
                            self.label_shown.emit(f'{label}\n{post_error}')
                            self.sig_doc_error.emit(i - deleted, post_error)
                        else:  # 정상 처리시
                            self.sig_doc_remove.emit(i - deleted)
                            deleted += 1
                        if self.diff_done == 'quit':
                            self.label_shown.emit('편집 비교 중 작업을 중단하였습니다.')
                            break
                    else:
                        self.label_shown.emit('첫 행에 편집 사항이 지정되어있지 않습니다.')
                        break
                if i == len(self.doc_list) - 1:  # 마지막 행
                    self.label_shown.emit('작업이 모두 완료되었습니다.')
            doc_logger.close()
            edit_logger.close()
        self.finished.emit()

    def edit(self, doc_code, doc_name, replacer):
        # 획득
        text_before, baserev, identifier, error = self.get_text(doc_code)
        if not error:  # 권한 X, 문서 X
            # 변경
            text_after, summary = replacer.send(text_before)
            # 비교
            self.sig_enable_pause.emit(False)
            changed, error = self.diff(text_before, text_after)
            self.sig_enable_pause.emit(True)
            if not error:
                if changed:
                    if self.after_diff:
                        text_after = self.after_diff
                if text_after:
                    error = self.post(doc_code, text_after, baserev, identifier, summary)  # 서버 오류 메시지
                else:
                    error = '문서에 내용이 없습니다.'
        return baserev, error

    def revert(self, doc_code, edit_list, rev_on_doc):
        rev, summary, error_log = '', '', ''
        for edit in edit_list:
            rev = ''
            if edit[1] == '복구':
                if edit[3] == '직전':
                    _, soup = self.requester.request_d('get', f'{SITE_URL}/history/{doc_code}')
                    if edit[4] == '현재':
                        href = soup.select_one('ul > li:nth-child(2) > span.t > a:nth-child(4)').get('href')
                        rev = href[href.find('?rev=') + 5:]
                    elif edit[4] == '마지막' or edit[4] == '처음':
                        usernames = [v.text for v in soup.select('ul > li > div > div > a')]
                        targets = [v.get('href')[v.get('href').find('?rev=') + 5:]
                                   for v in soup.select('ul > li > span.t > a:nth-child(4)')]
                        if edit[4] == '마지막':
                            b = False
                            for u, t in zip(usernames, targets):
                                if b:
                                    rev = t
                                    break
                                elif u == edit[5]:
                                    b = True
                        elif edit[4] == '처음':
                            b = ''
                            for u, t in zip(reversed(usernames), reversed(targets)):
                                if u == edit[5]:
                                    rev = b
                                    break
                                else:
                                    b = t
                elif edit[3] == '지정':
                    if edit[4] == '로그':
                        if rev_on_doc:
                            if rev_on_doc[0] == 'r' and rev_on_doc[1:].isdigit():
                                rev = rev_on_doc[1:]
                    else:  # '입력'
                        rev = edit[5]
            elif edit[1] == '요약':
                summary = edit[5]
        if not rev:
            error_log = '되돌릴 리비전을 찾지 못했습니다.'
        else:
            while True:
                _, soup = self.requester.request_d(
                    'post', f'{SITE_URL}/revert/{doc_code}',
                    data={'rev': rev, 'identifier': f'm:{self.requester.config.c["login"]["ID"]}', 'log': summary})
                if soup.h1.text == '오류':
                    error_temp = soup.select('article > div')[0].text
                    if error_temp == 'reCAPTCHA 인증이 실패했습니다.':  # 로그인 확인용
                        self.requester.login()
                    else:
                        error_log = error_temp
                        break
                else:
                    break
        return error_log

    def upload(self, file_dir, doc_name, text, summary):
        self.sig_enable_pause.emit(False)
        changed, error = self.diff('', text)
        self.sig_enable_pause.emit(True)
        if changed:
            if self.after_diff:
                text = self.after_diff
            else:
                error = '문서에 내용이 없습니다.'
        if not summary:
            summary = f'파일 {file_dir[file_dir.rfind("/") + 1:]}을 올림'
        if not error:  # 건너 뜀, 중단함
            multi_data = {'baserev': '0', 'identifier': f'm:{self.requester.config.c["login"]["ID"]}',
                          'document': doc_name, 'log': summary, 'text': text}
            try:
                with open(file_dir, 'rb') as f:
                    while True:
                        _, soup = self.requester.request_d('post', f'{SITE_URL}/Upload', data=multi_data, files={'file': f})
                        if self.is_captcha(soup):
                            self.requester.login()
                        else:
                            break
                error = self.has_alert(soup)
            except FileNotFoundError:
                error = '파일을 찾을 수 없습니다.'
        # self.post_log(file_dir, doc_name, '0', error_log)
        return error

    @staticmethod
    def upload_text(edit_list, summary=''):
        data = {'cite': '', 'date': '', 'author': '', 'etc': '', 'explain': '',
                'lic': '제한적 이용', 'cat': '파일/미분류'}
        for edit in edit_list:
            if edit[1] == '파일':
                if edit[3] == '본문':
                    if edit[4] == '출처':
                        data['cite'] = edit[5]
                    elif edit[4] == '날짜':
                        data['date'] = edit[5]
                    elif edit[4] == '저작자':
                        data['author'] = edit[5]
                    elif edit[4] == '기타':
                        data['etc'] = edit[5]
                    elif edit[4] == '설명':
                        data['explain'] = edit[5]
                elif edit[3] == '분류:':
                    data['cat'] = edit[5]
                elif edit[3] == '라이선스':
                    data['lic'] = edit[5]
            elif edit[1] == '요약':
                summary = edit[5]
        return f'[include(틀:이미지 라이선스/{data["lic"]})]\n[[분류:{data["cat"]}]]' \
               f'\n[목차]\n\n== 기본 정보 ==\n|| 출처 || {data["cite"]} ||\n|| 날짜 || {data["date"]} ||' \
               f'\n|| 저작자 || {data["author"]} ||\n|| 저작권 || {data["lic"]} ||\n|| 기타 || {data["etc"]} ||' \
               f'\n\n== 이미지 설명 ==\n{data["explain"]}', summary


class Micro(ReqPost):
    # sig_do_edit = Signal(str)
    sig_doc_error = Signal(int, str)
    sig_text_view = Signal(str, str, bool)
    sig_image_view = Signal(str)
    sig_start_text_edit = Signal(str)
    sig_apply_text_edit = Signal(str)
    sig_enable_iterate = Signal(bool)
    finished = Signal()

    def __init__(self, requester):
        super().__init__(requester)
        self.row_from = 0
        self.doc_code = ''
        self.text = ''
        self.new_before = ''
        self.do_edit = False
        self.do_mode = ''
        self.editable_mode = False

    def work(self):
        if self.editable_mode:
            self.edit()
        else:
            self.view()

    def view(self):
        editable = False
        doc_name = parse.unquote(self.doc_code)
        if self.doc_code[0] == '$':  # 파일
            if len(doc_name) > 50:
                doc_name = f'...{doc_name[-50:]}'
            label = f'\'{doc_name[1:]}\' 파일을 열람중입니다.'
            self.sig_image_view.emit(self.doc_code[1:])
        else:
            if self.doc_code[0] == '#':  # 편집 지시자
                label = f'{doc_name[1:]}번 편집사항을 열람 중입니다.'
                text = f'{doc_name[1:]}번 편집사항'
            elif self.doc_code[0] == '!':  # 중단자
                label = '중단점을 열람 중입니다.'
                text = '중단점'
            else:  # 문서
                text, _, _, _ = self.get_text(self.doc_code)
                # data = self.get_text(self.doc_code)
                if text:
                    label = f'<a href=\"{SITE_URL}/w/{self.doc_code}\">{doc_name}</a> 문서를 열람 중입니다.'
                    editable = True
                else:  # 문서 존재 X
                    label = f'\'{doc_name}\' 문서는 존재하지 않습니다.'
                    text = '존재하지 않는 문서입니다.'
            self.sig_text_view.emit(self.doc_code, text, editable)
        self.label_shown.emit(label)
        self.finished.emit()

    def edit(self):
        self.do_edit = False
        self.text, self.new_before = '', ''
        text_before, baserev, identifier, error = self.get_text(self.doc_code)
        # data = self.get_text(self.doc_code)
        doc_name = parse.unquote(self.doc_code)
        if error:  # 권한 X
            self.label_shown.emit(f'<a href=\"{SITE_URL}/w/{self.doc_code}\">{doc_name}</a> 문서를 편집할 권한이 없습니다.')
            time.sleep(0.3)
        else:
            self.sig_enable_iterate.emit(False)
            self.sig_start_text_edit.emit(text_before)
            self.label_shown.emit(f'<a href=\"{SITE_URL}/w/{self.doc_code}\">{doc_name}</a> 문서를 편집 중입니다.')
            while True:
                while not self.do_edit:  # from TabMacro.micro_text_post
                    time.sleep(0.3)
                # receive에 의해 self.text, do_mode 결정, do_edit 루프 탈출
                if self.do_mode == 'apply':
                    changed, error = self.diff(self.new_before, self.text)
                    if changed:
                        self.sig_apply_text_edit.emit(self.after_diff)
                    self.do_edit = False
                elif self.do_mode == 'post':
                    changed, error = self.diff(text_before, self.text)
                    if changed:
                        self.text = self.after_diff
                        doc_logger = storage.write_csv('doc_log.csv', 'a', 'doc')
                        doc_logger.send(None)
                        if self.text:
                            error = self.post(self.doc_code, self.text, baserev, identifier)
                        else:
                            error = '문서에 내용이 없어 취소되었습니다.'
                        if error:
                            self.sig_doc_error.emit(self.row_from, error)
                        doc_logger.send({'code': self.doc_code, 'title': doc_name, 'rev': f'r{baserev}',
                                         'time': self.time_doc_log(), 'index': '', 'error': error})
                        doc_logger.close()
                        break
                    else:  # diff_done = 'no'
                        self.do_edit = False
                else:  # mode = exit
                    break
            self.sig_enable_iterate.emit(True)
        self.finished.emit()

    def apply(self, text_before, edit_list):
        self.new_before = text_before
        replacer = self.find_replace(edit_list)
        replacer.send(None)
        text_after, _ = replacer.send(text_before)
        self.receive('apply', True, t=text_after)

    def receive(self, m, e, t=''):
        self.text = t
        self.do_mode = m
        self.do_edit = e


class ReqGet(ReqBasic):
    send_code_list = Signal(list)
    sig_invoke_msgbox = Signal(int, int)
    finished = Signal()

    def __init__(self, requester, doc_insert):
        super().__init__(requester)
        self.is_quit = False
        self.option = 0
        self.mode = 0  # 직접 입력
        self.code = ''
        self.doc_insert = doc_insert
        self.total = 0
        self.yesno = None

    def work(self):
        if self.mode == 1:  # 클릭 얻기
            self.code = self.copy_url()
        self.doc_insert.send(None)
        if self.code:  # 직접 입력은 DocBoard.insert에서 quote되어 들어옴
            self.total = 0
            if self.option == 0:  # 1개
                code = self.get_one(self.code)
                if code:
                    self.doc_insert.send([code, parse.unquote(code), ''])
            elif self.option == 1:  # 역링크
                for code in self.get_backlink(self.code):
                    self.doc_insert.send([code, parse.unquote(code), ''])
            elif self.option == 2:  # 분류
                if parse.unquote(self.code)[:3] == '분류:':
                    for code in self.get_cat(self.code):
                        self.doc_insert.send([code, parse.unquote(code), ''])
                else:
                    self.label_shown.emit('해당 문서는 분류 문서가 아닙니다.')
            elif self.option == 3:  # 사용자 기여 목록
                if self.mode == 1:
                    # self.code = parse.unquote(self.code)
                    if self.code.startswith(f'{parse.quote("사용자")}:'):
                        self.code = self.code[self.code.rfind(':') + 1:]
                    else:
                        contrib = re.match(r'(ip|author)/(.*?)/document', self.code)
                        if contrib:
                            self.code = contrib.group(2)
                for code in self.get_contrib(self.code):
                    self.doc_insert.send([code, parse.unquote(code), ''])
            elif self.option == 4:  # 검색
                if self.mode == 1:
                    self.label_shown.emit('검색 내역은 우클릭으로 추가할 수 없습니다.')
                else:
                    for code in self.get_search(self.code):
                        self.doc_insert.send([code, parse.unquote(code), ''])
            elif self.option == 5:  # 파일
                if self.mode == 1:
                    self.label_shown.emit('이미지 파일은 우클릭으로 추가할 수 없습니다.')
        else:
            self.label_shown.emit('올바른 URL을 찾을 수 없습니다.')
        self.finished.emit()

    def copy_url(self):
        pyperclip.copy('')
        time.sleep(0.01)
        keyboard.send('e')
        time.sleep(0.01)
        pasted_url = pyperclip.paste()
        if pasted_url:
            return self.get_code(pasted_url)
        else:
            keyboard.send('esc')
            time.sleep(0.01)
            return ''

    @staticmethod
    def is_exist_read(soup):  # /w/
        if soup.select('article > div > p'):
            return False  # 존재하지 않는 문서
        else:
            return True

    @staticmethod
    def get_redirect(url):
        pass

    @staticmethod
    def get_code(url):
        if url.find(SITE_URL) >= 0:
            search = re.search(rf'{SITE_URL}/\w+/(.*?)($|#|\?)', url).group(1)
            if search:
                return search
            else:
                return ''
        else:
            return ''

    def get_one(self, doc_code):  # 존재여부 검사
        doc_name = parse.unquote(doc_code)
        _, soup = self.requester.request_d('get', f'{SITE_URL}/w/{doc_code}')
        if self.is_exist_read(soup):
            self.label_shown.emit(f'{self.lnk_doc(doc_code, doc_name)} 문서를 목록에 추가했습니다.')
            return doc_code
        else:
            self.label_shown.emit(f'{self.lnk_doc(doc_code, doc_name)} 문서는 존재하지 않습니다.')

    def get_backlink(self, doc_code):
        doc_name = parse.unquote(doc_code)
        _, soup = self.requester.request_d('get', f'{SITE_URL}/backlink/{doc_code}')
        for namespace in list(map(lambda x: parse.quote(x.get('value')), soup.select('select:nth-child(2) > option'))):
            tail = ''
            while True:
                if self.is_quit:
                    self.label_shown.emit(
                        f'정지 버튼을 눌러 중단되었습니다.<br>'
                        f'{self.lnk_doc(doc_code, doc_name)}의 {self.lnk_blk(doc_code)} 문서를 {self.total}개 가져왔습니다.')
                    return
                self.label_shown.emit(
                    f'{self.lnk_doc(doc_code, doc_name)}의 역링크 '
                    f'<a href=\"{SITE_URL}/backlink/{doc_code}?namespace={namespace}\">{parse.unquote(namespace)}</a> '
                    f'가져오는 중... ( + {self.total} )<br>{parse.unquote(tail[5:-1])}')
                _, soup = self.requester.request_d('get',
                                                   f'{SITE_URL}/backlink/{doc_code}?{tail}namespace={namespace}&flag=0')
                for v in soup.select('article > div > div > div > ul > li > a'):  # 표제어 목록
                    if not v.next_sibling[2:-1] == 'redirect':
                        yield v.get('href')[3:]
                        self.total += 1
                tail = soup.select('article > div > div > a')[3].get('href')  # 앞뒤 버튼 중 뒤 버튼
                if not tail:  # 없으면 다음 스페이스로
                    break
                else:
                    tail = tail[tail.find('?from=') + 1:tail.find('&') + 1]
                    # added = added[added.find('?from'):].replace('\'', '%27')
        if self.total:
            self.label_shown.emit(
                f'{self.lnk_doc(doc_code, doc_name)}의 {self.lnk_blk(doc_code)} 문서를 {self.total}개 가져왔습니다.')
        else:
            self.label_shown.emit(
                f'{self.lnk_doc(doc_code, doc_name)}의 {self.lnk_blk(doc_code)} 문서가 존재하지 않습니다.')

    def get_cat(self, doc_code):
        doc_name = parse.unquote(doc_code)
        _, soup = self.requester.request_d('get', f'{SITE_URL}/w/{doc_code}')
        spaces = soup.select('.cl')
        for i in range(len(spaces)):
            name = (lambda x: x[x.rfind(' ') + 1:])(spaces[i].select('h2')[0].text)
            self.label_shown.emit(
                f'{self.lnk_doc(doc_code, doc_name)}의 하위 {name} 가져오는 중... ( + {self.total} )')
            for v in spaces[i].select('ul > li > a'):
                yield v.get('href')[3:]
                self.total += 1
            if spaces[i].select('div > div > a'):  # 다음 버튼
                tail = (lambda x: x[x.find('?namespace='):])(spaces[i].select('div > div > a')[1].get('href'))
                while True:
                    if self.is_quit:
                        self.label_shown.emit(
                            f'정지 버튼을 눌러 중단되었습니다.<br>'
                            f'{self.lnk_doc(doc_code, doc_name)}에 분류된 문서를 {self.total}개 가져왔습니다.')
                        return
                    else:
                        self.label_shown.emit(
                            f'{self.lnk_doc(doc_code, doc_name)}의 하위 {name} 가져오는 중... ( + {self.total} )')
                        _, new_soup = self.requester.request_d('get', f'{SITE_URL}/w/{doc_code}{tail}')
                        for v in new_soup.select('.cl')[i].select('ul > li > a'):
                            yield v.get('href')[3:]
                            self.total += 1
                        tail = (lambda x: '' if x is None else x[x.find('?namespace='):])(
                            new_soup.select('.cl')[i].select('div > div > a')[1].get('href'))
                        if not tail:
                            break
        if self.total:
            self.label_shown.emit(f'{self.lnk_doc(doc_code, doc_name)}에 분류된 문서를 {self.total}개 가져왔습니다.')
        else:
            self.label_shown.emit(f'{self.lnk_doc(doc_code, doc_name)}에 분류된 문서가 없습니다.')

    def get_search(self, keyword):
        search_url = f'{SITE_URL}/Search?q=%22{keyword}%22'
        lnk_search = f'{parse.unquote(keyword)}의 <a href=\"{search_url}\">검색 결과</a>'
        _, soup = self.requester.request_d('get', search_url)
        num = int(re.search(r'전체 (.*?) 건', soup.select_one('article > div > div.s').text.strip()).group(1))
        self.yesno = None
        if num > 0:
            self.sig_invoke_msgbox.emit(num, ceil(num / 20))
            while self.yesno is None:
                time.sleep(0.3)
            if not self.yesno:
                self.label_shown.emit('검색 작업이 중단되었습니다.')
                return
            else:
                for i in range(1, ceil(num/20) + 1):
                    if self.is_quit:
                        self.label_shown.emit(f'정지 버튼을 눌러 중단되었습니다.<br>'
                                              f'{lnk_search}를 {self.total}개 가져왔습니다.')
                        return
                    self.label_shown.emit(f'{lnk_search}를 가져오는 중... ( + {self.total} )')
                    _, soup = self.requester.request_d('get', f'{search_url}&page={i}')
                    for code in list(map(lambda x: x.get('href')[3:], soup.select('article > div > section > div > h4 > a'))):
                        yield code
                        self.total += 1
        if self.total:
            self.label_shown.emit(f'{lnk_search}를 {self.total}개 가져왔습니다.')
        else:
            self.label_shown.emit(f'{lnk_search}가 없습니다.')


    def get_contrib(self, user_name):
        tail = ''
        if re.match(r'^(?:25[0-5]|2[0-4]\d|[0-1]?\d{1,2})(?:\.(?:25[0-5]|2[0-4]\d|[0-1]?\d{1,2})){3}$', user_name):
            contrib_url = f'{SITE_URL}/contribution/ip/{user_name}/document'
            lnk_user = f'{user_name}의 <a href=\"{contrib_url}\">기여 목록</a>'
        else:
            contrib_url = f'{SITE_URL}/contribution/author/{user_name}/document'
            lnk_user = f'{self.lnk_doc(f"%EC%82%AC%EC%9A%A9%EC%9E%90:{user_name}", parse.unquote(user_name))}의 ' \
                       f'<a href=\"{contrib_url}\">기여 목록</a>'
        while True:
            if self.is_quit:
                self.label_shown.emit(f'정지 버튼을 눌러 중단되었습니다.<br>'
                                      f'{lnk_user}을 {self.total}개 가져왔습니다.')
                return
            self.label_shown.emit(f'{lnk_user}을 가져오는 중... ( + {self.total} )')
            _, soup = self.requester.request_d('get', f'{contrib_url}{tail}')
            temp = set()
            for code in list(map(lambda x: x.get('href')[3:], soup.select('tr > td > a:nth-child(1)'))):
                if code not in temp:
                    temp.add(code)
                    yield code
                    self.total += 1
            tail = soup.select('article > div > div > div > a')[1].get('href')  # 앞뒤 버튼 중 뒤 버튼
            if not tail:
                break
            else:
                tail = tail[tail.find('?from='):]
        if self.total:
            self.label_shown.emit(f'{lnk_user}을 {self.total}개 가져왔습니다.')
        else:
            self.label_shown.emit(f'{lnk_user}이 존재하지 않습니다.')

    @staticmethod
    def lnk_doc(code, name):
        return f'<a href=\"{SITE_URL}/w/{code}\">{name}</a>'

    @staticmethod
    def lnk_blk(code):
        return f'<a href=\"{SITE_URL}/backlink/{code}\">역링크</a>'
