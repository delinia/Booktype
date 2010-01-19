import sputnik

from booki.editor import models

def getTOCForBook(book):
    from booki.editor import models

    results = []

    for chap in list(models.BookToc.objects.filter(book=book).order_by("-weight")):
        # is it a section or chapter
        if chap.chapter:
            results.append((chap.chapter.id, chap.chapter.title, chap.chapter.url_title, chap.typeof, chap.chapter.status.id))
        else:
            results.append(('s%s' % chap.id, chap.name, chap.name, chap.typeof))

    return results


def getHoldChapters(book_id):
    from django.db import connection, transaction

    cursor = connection.cursor()
    # where chapter_id is NULL that is the hold Chapter
    cursor.execute("select editor_chapter.id, editor_chapter.title, editor_chapter.url_title, editor_booktoc.chapter_id, editor_chapter.status_id from editor_chapter left outer join editor_booktoc on (editor_chapter.id=editor_booktoc.chapter_id)  where editor_chapter.book_id=%s;", (book_id, ))

    chapters = []
    for row in cursor.fetchall():
        if row[-2] == None:
            chapters.append((row[0], row[1], row[2], 1, row[4]))

    return chapters


def getAttachments(book):
    import os.path
    import Image

    def _getDimension(att):
        if att.attachment.name.endswith(".jpg"):
            try:
                im = Image.open(att.attachment.name)
                return im.size
            except:
                return (0, 0)
        return None
            

    attachments = [{"id":        att.id, 
                    "dimension": _getDimension(att), 
                    "status":    att.status.id, 
                    "name":      os.path.split(att.attachment.name)[1], 
                    "size":      att.attachment.size} 
                   for att in models.Attachment.objects.filter(book=book)]

    return attachments


def remote_init_editor(request, message, bookid):
    # TODO, NEKA GRESKA OVDJE

    book = models.Book.objects.get(id=bookid)

    ## get chapters

    chapters = getTOCForBook(book)
    holdChapters = getHoldChapters(bookid)

    ## get users
    def vidi(a):
        if a == request.sputnikID:
            return "<b>%s</b>" % a
        return a

    users = [vidi(m) for m in list(sputnik.rcon.smembers("sputnik:channel:%s:channel" % message["channel"]))]

    ## get workflow statuses
    statuses = [(status.id, status.name) for status in models.BookStatus.objects.filter(book=book).order_by("-weight")]

    ## get attachments
    attachments = getAttachments(book)

    ## get metadata
    metadata = [{'name': v.name, 'value': v.getValue()} for v in models.Info.objects.filter(book=book)]

    ## notify others
    sputnik.addMessageToChannel(request, "/chat/%s/" % bookid, {"command": "user_joined", "user_joined": request.user.username}, myself = False)

    ## get licenses
    licenses =  [(elem.abbrevation, elem.name) for elem in models.License.objects.all().order_by("name")]

    ## get online users
    onlineUsers = sputnik.rcon.smembers("sputnik:channel:%s:users" % message["channel"])
        
    if request.user.username not in onlineUsers:
        sputnik.rcon.sadd("sputnik:channel:%s:users" % message["channel"], request.user.username)
        onlineUsers.add(request.user.username)
  
        ## set notifications to other clients
        sputnik.addMessageToChannel(request, "/booki/book/%s/" % bookid, {"command": "user_add", "username": request.user.username})


    # for now, this is one big temp here

    import time, decimal, re
    _now = time.time()
    locks = {}

    for key in sputnik.rcon.keys("booki:*:locks:*"):
        lastAccess = sputnik.rcon.get(key)

        if decimal.Decimal("%f" % _now) - lastAccess <= 30:
            m = re.match("booki:(\d+):locks:(\d+):(\w+)", key)
            if m:
                if m.group(1) == bookid:
                    locks[m.group(2)] = m.group(3)
                
    return {"licenses": licenses, 
            "chapters": chapters, 
            "metadata": metadata, 
            "hold": holdChapters, 
            "users": users, 
            "locks": locks, 
            "statuses": statuses, 
            "attachments": attachments, 
            "onlineUsers": list(onlineUsers)}


def remote_attachments_list(request, message, bookid):
    book = models.Book.objects.get(id=bookid)

    attachments = getAttachments(book)
    
    return {"attachments": attachments}


def remote_chapter_status(request, message, bookid):

    if message["status"] == "normal":
        sputnik.rcon.delete("booki:%s:locks:%s:%s" % (bookid, message["chapterID"], request.user.username))
        
    sputnik.addMessageToChannel(request, "/booki/book/%s/" % bookid, {"command": "chapter_status", 
                                                                      "chapterID": message["chapterID"], 
                                                                      "status": message["status"], 
                                                                      "username": request.user.username})
    return {}


def remote_chapter_save(request, message, bookid):
    # TODO
    # put this outside in common module
    # or maybe even betterm put it in the Model

    chapter = models.Chapter.objects.get(id=int(message["chapterID"]))

    if message.get("minor", False) != True:
        history = models.ChapterHistory(chapter = chapter,
                                        content = chapter.content,
                                        user = request.user,
                                        comment = message.get("comment", ""))
        history.save()

        from booki.editor import common

        common.logBookHistory(book = chapter.book,
                              chapter = chapter,
                              chapter_history = history,
                              user = request.user,
                              args = {"comment": message.get("comment", "")},
                              kind = 'chapter_save')

    chapter.content = message["content"];
    chapter.save()

    sputnik.addMessageToChannel(request, "/chat/%s/" % bookid, {"command": "message_info", 
                                                                "from": request.user.username, 
                                                                "message": 'User %s has saved chapter "%s".' % (request.user.username, chapter.title)}, myself=True)
    
    if not message['continue']:
        sputnik.addMessageToChannel(request, "/booki/book/%s/" % bookid, {"command": "chapter_status", 
                                                                          "chapterID": message["chapterID"], 
                                                                          "status": "normal", 
                                                                          "username": request.user.username})
        
        sputnik.rcon.delete("booki:%s:locks:%s:%s" % (bookid, message["chapterID"], request.user.username))

    return {}

    
def remote_chapter_rename(request, message, bookid):

    chapter = models.Chapter.objects.get(id=int(message["chapterID"]))
    oldTitle = chapter.title
    chapter.title = message["chapter"];
    chapter.save()

    from booki.editor import common
    common.logBookHistory(book = chapter.book,
                          chapter = chapter,
                          user = request.user,
                          args = {"old": oldTitle, "new": message["chapter"]},
                          kind = "chapter_rename")
    
    sputnik.addMessageToChannel(request, "/chat/%s/" %  bookid, {"command": "message_info", 
                                                                 "from": request.user.username, 
                                                                 "message": 'User %s has renamed chapter "%s" to "%s".' % (request.user.username, oldTitle, message["chapter"])}, 
                                myself=True)
    
    sputnik.addMessageToChannel(request, "/booki/book/%s/" % bookid, {"command": "chapter_status", 
                                                                      "chapterID": message["chapterID"], 
                                                                      "status": "normal", 
                                                                      "username": request.user.username})

    sputnik.addMessageToChannel(request, "/booki/book/%s/" % bookid, {"command": "chapter_rename", 
                                                                      "chapterID": message["chapterID"], 
                                                                      "chapter": message["chapter"]})
    
    return {}


def remote_chapters_changed(request, message, bookid):
    lst = [chap[5:] for chap in message["chapters"]]
    lstHold = [chap[5:] for chap in message["hold"]]

    book = models.Book.objects.get(id=bookid)

    weight = len(lst)

    from booki.editor import common
    common.logBookHistory(book = book,
                          user = request.user,
                          kind = "chapter_reorder")

    for chap in lst:
        if chap[0] == 's':
            m =  models.BookToc.objects.get(id__exact=int(chap[1:]))
            m.weight = weight
            m.save()
        else:
            try:
                m =  models.BookToc.objects.get(chapter__id__exact=int(chap))
                m.weight = weight
                m.save()
            except:
                chptr = models.Chapter.objects.get(id__exact=int(chap))
                m = models.BookToc(book = book,
                                   name = "SOMETHING",
                                   chapter = chptr,
                                   weight = weight,
                                   typeof=1)
                m.save()

        weight -= 1

    if message["kind"] == "remove":
        if type(message["chapter_id"]) == type(u' ') and message["chapter_id"][0] == 's':
            m =  models.BookToc.objects.get(id__exact=message["chapter_id"][1:])
            m.delete()
        else:
            m =  models.BookToc.objects.get(chapter__id__exact=int(message["chapter_id"]))
            m.delete()

#        addMessageToChannel(request, "/chat/%s/%s/" % (projectid, bookid), {"command": "message_info", "from": request.user.username, "message": 'User %s has rearranged chapters.' % request.user.username})

    sputnik.addMessageToChannel(request, "/booki/book/%s/" % bookid, {"command": "chapters_changed", 
                                                                      "ids": lst, 
                                                                      "hold_ids": lstHold, 
                                                                      "kind": message["kind"], 
                                                                      "chapter_id": message["chapter_id"]})
    return {}


def remote_get_users(request, message, bookid):
    res = {}
    def vidi(a):
        if a == request.sputnikID:
            return "!%s!" % a
        return a

    res["users"] = [vidi(m) for m in list(sputnik.rcon.smembers("sputnik:channel:%s:channel" % message["channel"]))]
    return res 


def remote_get_chapter(request, message, bookid):
    res = {}
    
    chapter = models.Chapter.objects.get(id=int(message["chapterID"]))
    res["title"] = chapter.title
    res["content"] = chapter.content 


    import time

    # set the initial timer for editor
    sputnik.rcon.set("booki:%s:locks:%s:%s" % (bookid, message["chapterID"], request.user.username), time.time())

    sputnik.addMessageToChannel(request, "/booki/book/%s/" % bookid, {"command": "chapter_status", 
                                                                      "chapterID": message["chapterID"], 
                                                                      "status": "edit", 
                                                                      "username": request.user.username})

    return res


def remote_book_notification(request, message, bookid):
    res = {}
    
    import time

    # rcon.delete(key)
    # set the initial timer for editor
    sputnik.rcon.set("booki:%s:locks:%s:%s" % (bookid, message["chapterID"], request.user.username), time.time())

    return res


def remote_chapter_split(request, message, bookid):
    book = models.Book.objects.get(id=bookid)


    from booki.editor import common
    common.logBookHistory(book = book,
                          user = request.user,
                          kind = 'chapter_split')
    
    allChapters = []

    try:
        originalChapter = models.Chapter.objects.get(id=int(message["chapterID"]))
    except:
        originalChapter = None
        
    try:
        tocChapter = models.BookToc.objects.get(book=book, chapter__id__exact=message["chapterID"])
    except:
        tocChapter = None

    import datetime
    from django.template.defaultfilters import slugify

    if tocChapter:
        allChapters = [chap for chap in models.BookToc.objects.filter(book=book).order_by("-weight")]
        initialPosition =  len(allChapters)-tocChapter.weight
    else:
        initialPosition = 0

    s = models.BookStatus.objects.filter(book=book).order_by("weight")[0]

    n = 0
    for chap in message["chapters"]:
        chapter = models.Chapter(book = book,
                                 url_title = slugify(chap[0]),
                                 title = chap[0],
                                 status = s,
                                 content = '<h1>%s</h1>%s' % (chap[0], chap[1]),
                                 created = datetime.datetime.now(),
                                 modified = datetime.datetime.now())
        chapter.save()

        if tocChapter:
            m = models.BookToc(book = book,
                               chapter = chapter,
                               name = chap[0],
                               weight = 0,
                               typeof = 1)
            m.save()
            allChapters.insert(1+initialPosition+n, m)

        n += 1

    if originalChapter:
        sputnik.addMessageToChannel(request, "/chat/%s/" % bookid, {"command": "message_info", "from": request.user.username, "message": 'User %s has split chapter "%s".' % (request.user.username, originalChapter.title)}, myself=True)

        originalChapter.delete()

    if tocChapter:
        tocChapter.delete()

    n = len(allChapters)
    for chap in allChapters:
        try:
            chap.weight = n
            chap.save()
            n -= 1
        except:
            pass

    ## get chapters

    chapters = getTOCForBook(book)
    holdChapters =  getHoldChapters(bookid)
        
    sputnik.addMessageToChannel(request, "/booki/book/%s/" % bookid, {"command": "chapter_split", 
                                                                      "chapterID": message["chapterID"], 
                                                                      "chapters": chapters, 
                                                                      "hold": holdChapters, 
                                                                      "username": request.user.username}, 
                                myself = True)
            
    return {}


def remote_create_chapter(request, message, bookid):
    import datetime

    book = models.Book.objects.get(id=bookid)

    from django.template.defaultfilters import slugify

    url_title = slugify(message["chapter"])

    # here i should probably set it to default project status
    s = models.BookStatus.objects.filter(book=book).order_by("weight")[0]

    chapter = models.Chapter(book = book,
                             url_title = url_title,
                             title = message["chapter"],
                             status = s,
                             content = '<h1>%s</h1>' % message["chapter"],
                             created = datetime.datetime.now(),
                             modified = datetime.datetime.now())
    chapter.save()

    from booki.editor import common
    common.logBookHistory(book = book,
                          chapter = chapter,
                          user = request.user,
                          kind = 'chapter_create')

    result = (chapter.id, chapter.title, chapter.url_title, 1, s.id)

    sputnik.addMessageToChannel(request, "/chat/%s/" % bookid, {"command": "message_info", 
                                                                "from": request.user.username, 
                                                                "message": 'User %s has created new chapter "%s".' % (request.user.username, message["chapter"])}, 
                        myself=True)

    sputnik.addMessageToChannel(request, "/booki/book/%s/" % bookid, {"command": "chapter_create", "chapter": result}, myself = True)

    return {}


def remote_publish_book(request, message, bookid):
    book = models.Book.objects.get(id=bookid)

    sputnik.addMessageToChannel(request, "/chat/%s/" % bookid, {"command": "message_info", 
                                                                "from": request.user.username, 
                                                                "message": '"%s" is being published.' % (book.title, )}, 
                        myself=True)

    import urllib2
    urlPublish = "http://objavi.flossmanuals.net/objavi.cgi"
#        urlPublish = "http://objavi.halo.gen.nz/objavi.cgi"

    publishMode = message.get("publish_mode", "epub")
    destination = "nowhere"

    if message.get("is_archive", False):
        destination = "archive.org"

    # TODO
    # FIX THIS
    f = urllib2.urlopen("%s?book=%s&project=export&mode=%s&server=booki.flossmanuals.net&destination=%s" % (urlPublish, book.url_title, publishMode, destination))
    ta = f.read()
    lst = ta.split("\n")
    dta, dtas3 = "", ""

    if len(lst) > 0:
        dta = lst[0]

        if len(lst) > 1:
            dtas3 = lst[1]

    return {"dtaall": ta, "dta": dta, "dtas3": dtas3}
            

def remote_create_section(request, message, bookid):
    import datetime
    book = models.Book.objects.get(id=bookid)

    c = models.BookToc(book = book,
                       name = message["chapter"],
                       chapter = None,
                       weight = 0,
                       typeof=0)
    c.save()

    from booki.editor import common
    common.logBookHistory(book = book,
                          user = request.user,
                          args = {"title": message["chapter"]},
                          kind = 'section_create')
                          

    result = ("s%s" % c.id, c.name, None, c.typeof)

    sputnik.addMessageToChannel(request, "/chat/%s/" % bookid, {"command": "message_info", 
                                                                "from": request.user.username, 
                                                                "message": 'User %s has created new section "%s".' % (request.user.username, message["chapter"])}, 
                                myself=True)

    sputnik.addMessageToChannel(request, "/booki/book/%s/" %  bookid, {"command": "chapter_create", 
                                                                       "chapter": result, 
                                                                       "typeof": c.typeof}, 
                                myself = True)
    
    return {}


def remote_get_history(request, message, bookid):
    import datetime

    book = models.Book.objects.get(id=bookid)

    book_history = models.BookHistory.objects.filter(book=book).order_by("-modified")

    history = []
    for entry in book_history:
        history.append({"modified": entry.modified.isoformat(), "description": entry.args, "user": entry.user.username, "kind": entry.kind})

    return {"history": history}

