# views.py
#
# 29 Dec 2014
# Australian Informatics Olympiad Committee
# Joshua Lau (junkbot)
#
# Views for Project Lorikeet.
# TODO(junkbot): Refactor helper functions to be classmethods of the class that
# they return.
# TODO(junkbot): Fix unicode issues.

from lorikeet import app
from flask import render_template, url_for, make_response, request, redirect
import groups

import psycopg2
import base64
import time

from collections import OrderedDict

_DATABASE_NAME = 'train'
_HARD_LIMIT = 100

# Information about a user that we might actually care about
class User(object):
    def __init__(self, userid=-1, username='', firstname='', lastname='',
        school='', year='', state='', country=''):
        self.userid = userid
        self.username = username
        self.firstname = firstname.decode('ascii', 'ignore')
        self.lastname = lastname.decode('ascii', 'ignore')
        self.school = school.decode('ascii', 'ignore')
        self.year = year
        self.state = state
        self.country = country

# Information about a problem that we might actually care about
class Problem(object):
    def __init__(self, problemid=-1, name='', title=''):
        self.problemid = problemid
        self.name = name
        self.title = title.decode('ascii', 'ignore')

class SubmissionScoreSummary(object):
    def __init__(self, user=None, problem=None, mark=None):
        self.user = user
        self.problem = problem
        self.mark = mark

# Information about a submission that we might actually care about
class SubmissionSummary(SubmissionScoreSummary):
    def __init__(self, user=None, problem=None, attempt=0, mark=0, timestamp=''):
        super(SubmissionSummary, self).__init__(user, problem, mark)
        self.attempt = attempt

        # TODO(junkbot): Make this faster. Getting number of attempts is
        # currently really, really slow.
        self.num_attempts = get_num_attempts(user.userid, problem.problemid)
        self.timestamp = timestamp

# Full submission details including source, judging output and language
class Submission(SubmissionSummary):
    def __init__(self, user=None, problem=None, attempt=0, mark=0,
        timestamp='', source='', lang='', langid='', judge=''):
        super(Submission, self).__init__(user, problem, attempt, mark,
            timestamp)
        self.source = source.strip().decode('ascii', 'ignore')
        self.lang = lang
        self.langid = langid
        self.judge = judge

class ProblemSetBrief(object):
    def __init__(self, name='', title='', public=False):
        self.name = name
        self.title = title
        self.public = public

# Information about a set that we might actually care about
class ProblemSet(ProblemSetBrief):
    def __init__(self, name='', title='', public=False, problems=[]):
        super(ProblemSet, self).__init__(name, title, public)
        self.problems = problems

# Extends the ProblemSet class by storing the overall mark for this set, and an
# array of SubmissionSummary objects describing the scores for each of the
# constituent problems.
class ProblemSetScores(ProblemSet):
    def __init__(self, name='', title='', public=False, problems=[], mark=None,
        subs=None):
        super(ProblemSetScores, self).__init__(name, title, public, problems)
        self.mark = mark
        self.subs = subs

# Class that stores the result to a problem search query.
class ProblemSearchResult(object):
    def __init__(self, problem=None, sets=[]):
        self.problem = problem
        self.sets = sets

    @classmethod
    def from_problem(cls, problem=None):
        return cls(problem, sets_containing_problem(problem))

# Class that stores a list of users and a list of sets that define a group.
class Group(object):
    def __init__(self, name='', title='', users=None, sets=None):
        self.name = name
        self.title = title
        self.users = users
        self.sets = sets

    @classmethod
    def from_names(cls, name='', title='', usernames=[], setnames=[]):
        users = map(lambda x: get_user(username=x), usernames)
        sets = map(get_set, setnames)
        return cls(name, title, users, sets)

# Class that stores a group and its associated marks.
class GroupMarks(Group):
    def __init__(self, group=None, marks=[]):
        super(GroupMarks, self).__init__(group.name, group.title, group.users,
            group.sets)
        self.marks = marks

# Given a list of users (by username), a list of sets (by name) and a list of
# problems (by name), return all submissions made by a user in the list and is
# either in the list of problems or belongs to a set in the list of sets. If
# users is empty, do not filter based on users. If sets and problems are both
# empty, do not filter based on problems at all. Orders in descending order of
# time (reverse chronological). Returns a list of SubmissionSummary objects.
# TODO(junkbot): Optimise this function's PSQL queries.
def filter_submissions(users=None, sets=None, problems=None):
    # Connect to database.
    conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
    cur = conn.cursor()

    # Shorthand for getting the first element in a tuple
    get_first = lambda x: x[0]

    # Work out which user ids we care about
    if users:
        query = 'SELECT id FROM competitors WHERE username=ANY(%s)'
        cur.execute(query, (users, ))
        user_ids = map(get_first, cur.fetchall())

    # Work out which problem ids we care about.
    if sets or problems:
        problem_ids = set()

        if sets:
            query = ('SELECT problemid FROM set_contents WHERE set=ANY(%s)')
            cur.execute(query, (sets, ))
            problem_ids |= set(map(get_first, cur.fetchall()))

        if problems:
            query = ('SELECT id FROM problems WHERE name=ANY(%s)')
            cur.execute(query, (problems, ))
            problem_ids |= set(map(get_first, cur.fetchall()))

        problem_ids = list(problem_ids)

    select_fields = [
        'competitorid',
        'problemid',
        'attempt',
        'mark',
        'timestamp'
    ]
    select_clause = 'SELECT DISTINCT %s ' % (', '.join(select_fields))

    if users and (sets or problems):
        query = select_clause + ('FROM submissions '
            'WHERE (competitorid=ANY(%s) AND problemid=ANY(%s)) '
            'ORDER BY timestamp DESC '
            'LIMIT %s;')
        cur.execute(query, (user_ids, problem_ids, _HARD_LIMIT, ))
    elif users:
        query = select_clause + ('FROM submissions '
            'WHERE competitorid=ANY(%s) '
            'ORDER BY timestamp DESC '
            'LIMIT %s;')
        cur.execute(query, (user_ids, _HARD_LIMIT, ))
    elif sets or problems:
        query = select_clause + ('FROM submissions '
            'WHERE problemid=ANY(%s) '
            'ORDER BY timestamp DESC '
            'LIMIT %s;')
        cur.execute(query, (problem_ids, _HARD_LIMIT, ))
    else:
        # 0.8s on limit 100
        query = select_clause + ('FROM submissions '
            'ORDER BY timestamp DESC '
            'LIMIT %s;')
        cur.execute(query, (_HARD_LIMIT, ))

    raw_results = cur.fetchall()
    results = []
    for r in raw_results:
        user = get_user(r[0])

        # 0.7s on limit 100
        problem = get_problem(r[1])
        attempt = int(r[2])

        # We need the or _DID_NOT_SCORE to handle cases when the judging was
        # terminated early and a mark was not assigned.
        try:
            mark = int(r[3])
        except TypeError:
            mark = None

        timestamp = r[4]

        # 0.7s on limit 100
        results.append(SubmissionSummary(user, problem, attempt, mark,
            timestamp))

    # Close database connection.
    cur.close()
    conn.close()

    return results

# TODO(junkbot): Make the PSQL queries more efficient. Possibly cache.
# Given a group, return a list for each set, each containing a list of
# ProblemSetScores containing the score details of each user for the set.
def get_group_scores(group=None):
    # Shorthand for getting the first element in a tuple
    get_first = lambda x: x[0]

    if group:
        # Connect to database.
        conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
        cur = conn.cursor()

        ret = [] 

        for s in group.sets:
            scores = []

            # Number of problems in the set.
            num_problems = len(s.problems)

            for user in group.users:
                tot = 0
                any_attempts = False
                subs = []
                for problem in s.problems:
                    # Find max score for this user for this problem, if any.
                    query = (
                        'SELECT bestscore '
                        'FROM progress '
                        'WHERE (competitorid=%s AND problemid=%s);'
                    )
                    cur.execute(query, (user.userid, problem.problemid, ))
                    raw_result = cur.fetchone()
                    if raw_result and get_first(raw_result) is not None:
                        sub = SubmissionScoreSummary(user, problem,
                                mark=int(get_first(raw_result)))
                    else:
                        sub = SubmissionScoreSummary(user, problem, mark=None)

                    subs.append(sub)

                    if sub.mark:
                        mark = int(sub.mark)
                        any_attempts = True
                    else:
                        mark = 0
                    tot += mark

                if any_attempts:
                    ave = tot/num_problems
                else:
                    ave = None
                scores.append(ProblemSetScores(s.name, s.title, s.public,
                    s.problems, ave, subs))

            ret.append(scores)

        # Close database connection.
        cur.close()
        conn.close()

        return ret
    else:
        return None

# Returns a User object based on competitorid or username None if user doesn't
# exist. If both are given, only competitorid is used.
def get_user(userid=None, username=None):
    # Connect to database.
    conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
    cur = conn.cursor()

    if userid or username:
        if userid:
            cur.execute('SELECT id, username, firstname, lastname, school, '
                'year, state, country FROM competitors WHERE id=%s', (userid, ))
        elif username:
            cur.execute('SELECT id, username, firstname, lastname, school, '
                'year, state, country FROM competitors WHERE username=%s',
                (username, ))
        raw_result = cur.fetchone()
        if raw_result:
            ret = User(*raw_result)
        else:
            ret = None
    else:
        ret = None

    # Close database connection.
    cur.close()
    conn.close()
    return ret

# Returns a Problem object based on problemid or problemname or None if problem
# doesn't exist. If both are given, only problemid is used.
def get_problem(problemid=None, problemname=None):
    # Connect to database.
    conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
    cur = conn.cursor()

    if problemid or problemname:
        if problemid:
            cur.execute('SELECT id, name, title '
                'FROM problems WHERE id=%s', (problemid, ))
        elif problemname:
            cur.execute('SELECT id, name, title '
                'FROM problems WHERE name=%s', (problemname, ))

        raw_result = cur.fetchone()
        if raw_result:
            ret = Problem(*raw_result)
        else:
            ret = None
    else:
        ret = None

    # Close database connection.
    cur.close()
    conn.close()
    return ret

# Returns a ProblemSet object based on setname or None if problem doesn't exist.
def get_set(setname=None):
    # Connect to database.
    conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
    cur = conn.cursor()

    if setname:
        cur.execute('SELECT name, title, public '
            'FROM sets WHERE name=%s', (setname, ))

        raw_result = cur.fetchone()
        if raw_result:
            cur.execute('SELECT problemid FROM set_contents WHERE set=%s;',
                (setname, ))
            problems = map(lambda x: get_problem(problemid=x[0]), cur.fetchall())
            ret = ProblemSet(*raw_result, problems=problems)
        else:
            ret = None
    else:
        ret = None

    # Close database connection.
    cur.close()
    conn.close()
    return ret

# Returns a Group object based on groupname or None if group doesn't exist.
# TODO(junkbot): Find a better way of specifying groups.
def get_group(groupname=None):
    return GROUPS.get(groupname)

# Returns the number of submissions a user has made to a problem.
# TODO(junkbot): Apparently this is a bottleneck. FIXME.
def get_num_attempts(userid, problemid):
    # Connect to database.
    conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
    cur = conn.cursor()

    query = ('SELECT COUNT(*) FROM submissions '
        'WHERE competitorid=%s AND problemid=%s;')
    cur.execute(query, (userid, problemid, ))
    ret = int(cur.fetchone()[0])

    # Close database connection.
    cur.close()
    conn.close()
    return ret

# Returns a Submission object based on username, problename and attempt or None
# if such a submission doesn't exist. 
def get_submission(username=None, problemname=None, attempt=None):
    try:
        # Convert attempt number to integer
        attempt = int(attempt)

        # Connect to database.
        conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
        cur = conn.cursor()

        user = get_user(username=username)
        problem = get_problem(problemname=problemname)

        if user and problem:
            query = ('SELECT s.attempt, s.mark, s.timestamp, s.submitted_file, '
                'l.name, l.id, s.judge '
                'FROM submissions s '
                'INNER JOIN languages l ON (s.languageid = l.id) '
                'WHERE s.competitorid=%s AND s.problemid=%s '
                'ORDER BY s.attempt ASC;')
            cur.execute(query, (user.userid, problem.problemid, ))
            raw_results = cur.fetchall()
            if (raw_results and attempt != 0
                and abs(attempt) <= len(raw_results)):
                if attempt > 0:
                    attempt -= 1
                raw_result = raw_results[attempt]
                ret = Submission(user, problem, *raw_result)
            else:
                ret = None
        else:
            ret = None

        # Close database connection.
        cur.close()
        conn.close()
    except ValueError:
        ret = None
    return ret

# Given a search string, looks for all users that contain that substring in
# username, firstname or lastname.
def user_search_query(substr):
    # Add literal % on either side of the string for PSQL-style regex.
    substr = '%%%s%%' % (substr.lower())
    space = ' '

    # Connect to database.
    conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
    cur = conn.cursor()

    query = ('SELECT id FROM competitors '
        'WHERE lower(username) LIKE %s OR '
        'lower(firstname) LIKE %s OR '
        'lower(lastname) LIKE %s OR '
        '(lower(firstname) || %s || lower(lastname)) LIKE %s '
        'ORDER BY firstname, lastname;')
    cur.execute(query, (substr, substr, substr, space, substr, ))
    ret = map(lambda x: get_user(userid=x[0]), cur.fetchall())

    # Close database connection.
    cur.close()
    conn.close()
    return ret

# Given a search string, looks for all problems that contain that substring in
# either name or title.
def problem_search_query(substr):
    # Add literal % on either side of the string for PSQL-style regex.
    substr = '%%%s%%' % (substr.lower())

    # Connect to database.
    conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
    cur = conn.cursor()

    query = ('SELECT id, name, title FROM problems WHERE '
        'lower(title) LIKE %s OR lower(name) LIKE %s '
        'ORDER BY id;')
    cur.execute(query, (substr, substr, ))
    ret = map(lambda x: ProblemSearchResult.from_problem(Problem(*x)),
        cur.fetchall())

    # Close database connection.
    cur.close()
    conn.close()
    return ret

# Gets stats for a given problem to display on the problem's page
def problem_stats(problemid=-1):
    conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
    cur = conn.cursor()
    stats = OrderedDict()

    # Total number of competitors who scored 100
    query = ('SELECT DISTINCT count(competitorid) '
             'FROM submissions '
             'WHERE problemid = %(problemid)s AND mark = 100;') % {'problemid': problemid}
    cur.execute(query)
    total_solves = cur.fetchone()[0]
    stats["Total Solves"] = total_solves

    # Total Submissions
    query = ('SELECT count(*) '
             'FROM submissions '
             'WHERE problemid = %(problemid)s;') % {'problemid': problemid}
    cur.execute(query)
    stats["Total Submissions"] = cur.fetchone()[0]

    # Average Submissions for people who solve the problem
    if total_solves == 0:
        stats["Average submissions per solve"] = "N/A"
    else:
        query = ('''SELECT count(t1.competitorid)
                    FROM submissions as t1
                    JOIN (
                        SELECT DISTINCT competitorid
                        FROM progress
                        WHERE problemid = %(problemid)s AND bestscore = 100
                    ) as t2
                    ON t1.competitorid = t2.competitorid
                    WHERE t1.problemid = %(problemid)s;''') % {'problemid': problemid}
        cur.execute(query)
        stats["Average submissions per solve"] = cur.fetchone()[0]/total_solves

    # How many people have viewed to the problem
    query = ('SELECT DISTINCT count(competitorid) '
             'FROM progress '
             'WHERE problemid = %(problemid)s;') % {'problemid': problemid}
    cur.execute(query)
    stats["Total users who've viewed this problem"] = cur.fetchone()[0]
    # Close database connection.
    cur.close()
    conn.close()
    return stats

# Given a search string, looks for all sets that contain that substring in name
# or title.
def set_search_query(substr):
    # Add literal % on either side of the string for PSQL-style regex.
    substr = '%%%s%%' % (substr.lower())

    # Connect to database.
    conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
    cur = conn.cursor()

    query = ('SELECT name, title, public FROM sets '
        'WHERE lower(name) LIKE %s OR lower(title) LIKE %s '
        'ORDER BY name;')
    cur.execute(query, (substr, substr, ))
    ret = map(lambda x: ProblemSetBrief(*x), cur.fetchall())

    # Close database connection.
    cur.close()
    conn.close()
    return ret

# Given a Problem object, return a list of ProblemSetBrief objects that
# represent all sets that contain this problem.
def sets_containing_problem(problem):
    # Connect to database.
    conn = psycopg2.connect('dbname=%s' % (_DATABASE_NAME))
    cur = conn.cursor()

    query = ('SELECT name, title, public '
        'FROM sets, set_contents '
        'WHERE sets.name = set_contents.set AND '
        'set_contents.problemid = %s '
        'ORDER BY sets.name;')
    cur.execute(query, (problem.problemid, ))
    ret = map(lambda x: ProblemSetBrief(*x), cur.fetchall())

    # Close database connection.
    cur.close()
    conn.close()
    return ret

@app.route('/')
def index():
    subs = filter_submissions()
    return render_template('index.html', subs=subs)

# Page for a user.
@app.route('/user/<username>/')
@app.route('/user/<username>')
def user_page(username):
    user = get_user(username=username)
    if user:
        subs = filter_submissions(users=[user.username])
        return render_template('user_page.html', user=user, subs=subs)
    else:
        return 'User does not exist'

# Page for a problem.
@app.route('/problem/<problemname>/')
@app.route('/problem/<problemname>')
def problem_page(problemname):
    problem = get_problem(problemname=problemname)
    if problem:
        # Get submissions
        subs = filter_submissions(problems=[problemname])

        # Get problem stats
        stats = problem_stats(problem.problemid)

        return render_template('problem_page.html', problem=problem, subs=subs, stats=stats)
    else:
        return 'Problem does not exist'

# Page for a set.
@app.route('/set/<setname>/')
@app.route('/set/<setname>')
def set_page(setname):
    sett = get_set(setname=setname)
    if sett:
        subs = filter_submissions(sets=[setname])
        return render_template('set_page.html', sett=sett, subs=subs)
    else:
        return 'Set does not exist'

# Page for a user's attempts at a problem.
@app.route('/user/<username>/problem/<problemname>/')
@app.route('/user/<username>/problem/<problemname>')
def user_problem(username, problemname):
    user = get_user(username=username)
    problem = get_problem(problemname=problemname)
    if user and problem:
        subs = filter_submissions(users=[username], problems=[problemname])
        return render_template('user_problem.html', user=user, problem=problem,
            subs=subs)
    else:
        return 'User or problem does not exist'

# Page for a set.
@app.route('/user/<username>/set/<setname>/')
@app.route('/user/<username>/set/<setname>')
def user_set_page(username, setname):
    user = get_user(username=username)
    sett = get_set(setname=setname)
    if user and sett:
        subs = filter_submissions(users=[username], sets=[setname])
        return render_template('user_set.html', user=user, sett=sett, subs=subs)
    else:
        return 'User or set does not exist'

# Page for a user's specific attempt at a problem.
@app.route('/user/<username>/problem/<problem>/<attempt>/')
@app.route('/user/<username>/problem/<problem>/<attempt>')
def user_problem_attempt(username, problem, attempt):
    sub = get_submission(username, problem, attempt)
    if sub:
        return render_template('submission.html', sub=sub)
    else:
        return 'Attempt doesn\'t exist.'
    
# Extract and download a copy of the source code of the attempt.
@app.route('/user/<username>/problem/<problem>/<attempt>/extract/')
@app.route('/user/<username>/problem/<problem>/<attempt>/extract')
def user_problem_attempt_extract(username, problem, attempt):
    sub = get_submission(username, problem, attempt)
    if sub:
        if sub.langid == 'zip':
            response = make_response(base64.b64decode(sub.source))
        else:
            response = make_response(sub.source)

        response.headers['Content-Disposition'] = (
            'attachment; filename=%s-%s-%d.%s'
        ) % (sub.user.username, sub.problem.name, sub.attempt, sub.langid)
        return response
    else:
        return 'Attempt doesn\'t exist.'
#@app.route('/filter')
#def filter_test():
#    q = filter_submissions(users=['junkbot','rayli'],
#        problems=['aiio13vitamin', 'aio11tickets'])
#    q = filter_submissions(users=['junkbot','rayli'])
#    q = filter_submissions(sets=['graph14decalpha1', 'graph14decalpha2'],
#        problems=['aiio13vitamin', 'aio11tickets'])
#    q = filter_submissions()
#    return str(q)

# Group scoreboard.
@app.route('/group/<groupname>/scoreboard/')
@app.route('/group/<groupname>/scoreboard')
def group_scoreboard(groupname):
    group = get_group(groupname)
    if group:
        groupMarks = GroupMarks(group, get_group_scores(group))
        response = render_template('group_scoreboard.html', group=groupMarks)
        return response
    else:
        return 'Group does not exist.'

# Recent group submissions. Can be for any problem; not necessarily one in the
# group's sets.
@app.route('/group/<groupname>/subs/')
@app.route('/group/<groupname>/subs')
def group_subs(groupname):
    group = get_group(groupname)
    if group:
        usernames = map(lambda x: x.username, group.users)
        subs = filter_submissions(users=usernames)
        return render_template('group_subs.html',group=group,subs=subs)
    else:
        return 'Group does not exist.'

# Group page for a problem. Doesn't matter if the problem is a part of one of
# that group's sets or not.
@app.route('/group/<groupname>/problem/<problemname>/')
@app.route('/group/<groupname>/problem/<problemname>')
def group_problem(groupname, problemname):
    group = get_group(groupname)
    problem = get_problem(problemname=problemname)
    if group and problem:
        usernames = map(lambda x: x.username, group.users)
        subs = filter_submissions(users=usernames, problems=[problemname])
        return render_template('group_problem.html', group=group, problem=problem,
            subs=subs)
    else:
        return 'Group or problem does not exist.'


# Group page for a set. Doesn't matter if the set is a part of that group or
# not.
@app.route('/group/<groupname>/set/<setname>/')
@app.route('/group/<groupname>/set/<setname>')
def group_set(groupname, setname):
    group = get_group(groupname)
    sett = get_set(setname=setname)
    if group and sett:
        usernames = map(lambda x: x.username, group.users)
        subs = filter_submissions(users=usernames, sets=[setname])
        return render_template('group_set.html',group=group,sett=sett,subs=subs)
    else:
        return 'Group or set does not exist.'

# Correctly route search queries.
@app.route('/search/handle')
def search_handle():
    query = request.args.get('query','')
    if request.args.get('users'):
        return redirect(url_for('search_user', query=query))
    elif request.args.get('problems'):
        return redirect(url_for('search_problem', query=query))
    else:
        return 'Invalid search query.'

# Search for a problem or set with url encoded query variable.
@app.route('/search/problem')
def search_problem():
    query = request.args.get('query','')
    problems_res = problem_search_query(query)
    sets_res = set_search_query(query)
    return render_template(
        'search_problem.html',
        query=query,
        sets_res=sets_res,
        problems_res=problems_res)

@app.route('/search/user')
def search_user():
    query = request.args.get('query','')
    users_res = user_search_query(query)
    return render_template(
        'search_user.html',
        query=query,
        users_res=users_res)

# Convert group.GROUPS into GROUPS.
GROUPS = dict(
    (key, Group.from_names(name=key, **params)) for (key, params) in
        groups.GROUPS.iteritems())
