"""Prepare résumé-grounded outreach drafts and conservative recipient choices."""
import csv,hashlib,json,re,unicodedata
from collections import Counter,defaultdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT.parent/'company_contacts.csv'
RESUME=Path('/Users/panththakkar/Documents/Resume/Panth_Thakkar_Resume_October_2026.pdf')
ATTACHMENT=RESUME.name
rows=list(csv.DictReader(SOURCE.open(encoding='utf-8-sig',newline='')))
assert len(rows)==1922 and RESUME.exists()
evidence=defaultdict(list)
for c in csv.DictReader((ROOT.parent/'contact_evidence.csv').open(encoding='utf-8-sig',newline='')):
 if c['type']=='email':evidence[(c['association'],c['member'],c['value'].lower())].append(c)

def norm(s):
 s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower().replace('&',' and ')
 return re.sub(r'[^a-z0-9]','',re.sub(r'\b(incorporated|inc|limited|ltd|corp|corporation|ltee|llc|ulc)\b',' ',s))
def host(u):return (urlsplit(u).hostname or '').lower().removeprefix('www.')
def related(email,website):
 a=email.rsplit('@',1)[-1].lower();b=host(website)
 return bool(a and b and (a==b or a.endswith('.'+b) or b.endswith('.'+a)))
def items(s):return list(dict.fromkeys(x.strip() for x in s.split('|') if x.strip()))
def display(s):
 return re.sub(r'\s+(?:Inc\.?|Ltd\.?|Limited|Corporation|Corp\.?|Ltee\.?|ULC|LLC)\s*$','',s,flags=re.I).strip()
def src(row,email):
 matches=evidence.get((row['association'],row['member'],email.lower()),[])
 return next((c['source_url'] for c in matches if c['source_url']), '')

verified={
 'terrasenseanalytics':('careers@terrasense.ca','https://www.terrasense.ca/careers',1),
 'kdmanalytics':('careers@kdmanalytics.com','https://kdmanalytics.com/about/careers/',2),
 'independentrobotics':('recruiting@independentrobotics.com','https://www.independentrobotics.com/careers',3),
 'compusult':('careers@compusult.com','https://www.compusult.com/careers',4),
}
blocked={
 'recruitment@durhamcollege.ca':'Published address is for student recruitment.',
 'recruitment@confederationcollege.ca':'Source is a visit/student recruitment page; employer-hiring purpose not confirmed.',
 'recruitment.indigenous@uoguelph.ca':'Source concerns undergraduate/student recruitment.',
 'careers.jmsb@concordia.ca':'Career-service contact, not confirmed as an employer recruitment inbox.',
 'hire.talent@uwaterloo.ca':'Employer/student talent-service contact, not confirmed as university staff hiring.',
 'recruitingfraud@moodys.com':'Recruitment-fraud reporting address.',
 'hr.servicescenter@nbc.ca':'HR service-centre address; recruitment purpose not confirmed.',
 'hr@universityofvictoria.us':'Recorded domain needs institution-identity review.',
}
exclude=re.compile(r'privacy|dpo|gdpr|abuse|fraud|phish|security[-_.]?incident|webmaster|noreply|no-reply|accessibility|accommodation|investor|press|media|communications|^ir$|^pr$|legal|complaint',re.I)
hr_pattern=re.compile(r'^(?:careers?|jobs?|recruit(?:ing|ment)?|hr|hiring|talent)(?:$|[._-])|^(?:corporate[._-]hr|hr[._-]recruit)',re.I)
general_pattern=re.compile(r'^(?:info|contact|enquiries|inquiries|enquiry|inquiry|hello|general|reception|office|admin)(?:$|[._-])',re.I)
free_domains={'gmail.com','hotmail.com','outlook.com','yahoo.com','live.com','proton.me','protonmail.com','aol.com'}

def recipient(row):
 key=norm(row['member']);notes=[]
 if 'avss' in key and 'aerialvehicle' in key:return '', '', '', 'Existing interview thread','Use the existing AVSS interview contact; it was not provided in this chat.',notes
 if row['member']=='Dr. Desmond G. Newman':return '', 'Dr. Newman','', 'Professional networking','No verified email selected for this individual member.',notes
 if key in verified:
  email,url,_=verified[key];return email,'',url,'Recruiting inbox','Verified on official careers page on November 5, 2026.',notes
 if 'existing website/contact match needs review' in row['headquarters_review_status'].lower() or key=='universityofvictoria':
  return '', '', '', 'Recipient research required','Company/institution identity must be confirmed before selecting a recipient.',notes
 emails=items(row['public_contact_emails']);hr=[];general=[];people=[]
 for email in emails:
  if not re.fullmatch(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+',email):continue
  email=email.lower();local,domain=email.rsplit('@',1)
  cs=evidence.get((row['association'],row['member'],email),[])
  context=' '.join(c['context']+' '+c['source_url'] for c in cs)
  if email in blocked:notes.append(email+': '+blocked[email]);continue
  if exclude.search(local):notes.append(email+': specialized/support function excluded from recruitment selection.');continue
  if 'square-enix.com' in domain and 'square enix' not in row['member'].lower():notes.append(email+': unrelated company domain excluded.');continue
  if re.search(r'middleeast|vietnam|braila|teisnach|ipoque',email,re.I):notes.append(email+': region/subsidiary-specific address requires location/entity confirmation.');continue
  if domain in free_domains:continue
  owned_source=any(host(c['source_url'])==host(row['likely_company_homepage']) for c in cs)
  if hr_pattern.search(local):
   if re.search(r'/admission|/student-recruit|career.services|/contacts/visit',context,re.I):notes.append(email+': student-facing source excluded.');continue
   if related(email,row['likely_company_homepage']) or owned_source:
    if re.search(r'accommodation|accessibility',context,re.I) and not re.search(r'send.{0,30}(resume|cv)|applications? (to|via)|employment opportunit',context,re.I):notes.append(email+': source may concern application assistance; recruiting purpose needs review.');continue
    hr.append(email)
  elif general_pattern.search(local) and related(email,row['likely_company_homepage']):general.append(email)
  elif related(email,row['likely_company_homepage']):
   for person in items(row['named_business_contacts']):
    name=person.split(' — ')[0].strip();parts=re.findall(r'[A-Za-z]+',name)
    if len(parts)<2:continue
    first,last=parts[0].lower(),parts[-1].lower();address=re.sub(r'[^a-z]','',local)
    if address in {first+last,first[0]+last} and any(first in c['context'].lower() and last in c['context'].lower() for c in cs):people.append((email,name));break
 if hr:
  selected=sorted(hr,key=lambda x:('queries' in x,len(x)))[0]
  return selected,'',src(row,selected),'Recruiting inbox','Published recruiting contact; verify current purpose, company, and region before use.',notes
 if general:
  selected=sorted(general,key=lambda x:('info'!=x.split('@')[0],len(x)))[0]
  return selected,'',src(row,selected),'General business inbox','General business address; draft requests the appropriate hiring contact.',notes
 if people:
  selected,name=people[0];return selected,name,src(row,selected),'Named business contact','Published business contact, not confirmed as a recruiter; draft requests routing.',notes
 pages=items(row['contact_page_urls'])
 page=next((u for u in pages if not re.search(r'privacy|legal|investor|media|press|accessibility',u,re.I)), '')
 if page:return '', '', page, 'Contact page','No suitable email selected. The routing draft can be adapted for the published contact page.',notes
 return '', '', '', 'Recipient research required','No suitable recruiting or general business email confirmed.',notes

SIGNATURE='Best regards,\nPanth Thakkar\n647-523-6680\npanth.thakkar@outlook.com\nGitHub: https://github.com/PanthT\nLinkedIn: https://www.linkedin.com/in/panththakkar'
def category(row):
 n=row['member'].lower()
 if 'avss' in n and 'aerial vehicle' in n:return 'Existing AVSS interview'
 if row['member']=='Dr. Desmond G. Newman':return 'Individual networking'
 if re.search(r'staffing|recruit|personnel|technical solutions|talent acquisition',n):return 'Recruiting agency inquiry'
 if re.search(r'association|alliance|economic development|chamber|trade.*partnership|aerospace council',n):return 'Industry-network inquiry'
 if re.search(r'university|universit[ée]|college|polytechnic|polytechnique|institute of technology',n):return 'Institutional IT inquiry'
 if re.search(r'cyber|infosec|security|secure|securit|risk|sapper|palitronica|kdm analytics',n):return 'Cybersecurity and automation'
 if re.search(r'robot|drone|aero|avion|aircraft|rocket|space|unmanned|autonomous|terrasense|skycope|naval|marine|sensor|telemetry',n):return 'Technical systems and integration'
 if re.search(r'software|digital|cloud|analytics|informatics|computing|data systems',n):return 'Software and cloud automation'
 return 'Infrastructure and security'

def draft(row,kind,to_name,channel):
 company=display(row['member']);greeting=f'Hello {to_name},' if to_name else f'Dear {company} Hiring Team,'
 signature=SIGNATURE
 if kind=='Existing AVSS interview':
  return ('AVSS technical role — follow-up — Panth Thakkar',f'Hello,\n\nThank you for considering me for the technical opportunity at AVSS. I remain very interested in the role and in contributing my systems and security experience to the team.\n\nMy ongoing drone-detection and rocket-telemetry projects have strengthened my interest in practical work that connects software, hardware, and reliable data collection. I would be happy to share further project details or documentation if useful.\n\nPlease let me know if there is any additional information I can provide to support the next steps.\n\n{signature}','')
 if kind=='Individual networking':
  return ('Advice on technical career opportunities — Panth Thakkar',f'Dear Dr. Newman,\n\nI am exploring technical opportunities across infrastructure, cybersecurity, automation, and aerospace-related systems, and hoped you might be willing to offer some guidance.\n\nI hold an M.S. in Information Technology from Arizona State University. My experience at TELUS, IESO, and Acumen Engineered Solution Inc. includes application modernization, network and security infrastructure, vulnerability assessment, and Python automation. My personal projects include drone detection and rocket telemetry.\n\nIf you are comfortable doing so, could you suggest relevant employers, professional contacts, or a suitable way to learn about opportunities in the sector? I would be happy to share my résumé for context.\n\nThank you for your time and consideration.\n\n{signature}','')
 if kind=='Industry-network inquiry':
  return (f'Technical opportunities and industry contacts — {company}',f'Hello {company} Team,\n\nI am exploring opportunities in infrastructure, cybersecurity, Python automation, and technical systems, and would appreciate any guidance your team can offer.\n\nI hold an M.S. in Information Technology from Arizona State University. Through roles at TELUS, IESO, and Acumen Engineered Solution Inc., I have worked on application modernization, network and security infrastructure, vulnerability assessment, and automation.\n\nCould you point me toward relevant member employers, hiring contacts, or career resources? I would also be interested in suitable technical opportunities within your own organization.\n\nI have attached my résumé for context. Thank you for any direction you can provide.\n\n{signature}',ATTACHMENT)
 if kind=='Recruiting agency inquiry':
  return (f'Infrastructure and cybersecurity candidate — Panth Thakkar — {company}',f'{greeting}\n\nI am reaching out to ask whether you are recruiting for infrastructure, networking, cybersecurity, Python automation, or technical support roles suited to my background.\n\nI hold an M.S. in Information Technology from Arizona State University. My experience at TELUS, IESO, and Acumen Engineered Solution Inc. spans application modernization, network and security hardware deployment, vulnerability assessment, and automation. I have worked with Python, Linux, Cisco switches, Check Point firewalls, and cloud deployment tools.\n\nI have attached my résumé and would welcome consideration for relevant current or upcoming opportunities. Could you let me know the best recruiter or team to speak with?\n\n{signature}',ATTACHMENT)
 if channel in ('General business inbox','Named business contact','Contact page','Recipient research required'):
  greeting=f'Hello {to_name},' if to_name else f'Hello {company} Team,'
  return (f'Appropriate technical hiring contact at {company}',f'{greeting}\n\nI am exploring technical opportunities at {company} and hoped you could point me to the appropriate hiring contact.\n\nMy background includes infrastructure and security work at IESO and Acumen Engineered Solution Inc., application modernization at TELUS, and an M.S. in Information Technology from Arizona State University. I am particularly interested in infrastructure, cybersecurity, Python automation, or technical systems roles.\n\nI have attached my résumé for context. If recruitment sits with another team, would you be comfortable forwarding my inquiry or sharing the appropriate recruiting address or application page?\n\nThank you for your time.\n\n{signature}',ATTACHMENT)
 if kind=='Technical systems and integration':
  opening=f'I am reaching out about current or upcoming technical systems, integration, testing, or software-support opportunities at {company}.'
  evidence_text='My experience at TELUS, IESO, and Acumen Engineered Solution Inc. includes application modernization, infrastructure and security work, and Python automation. I hold an M.S. in Information Technology from Arizona State University.'
  detail='Alongside my professional experience, I am developing a Jetson Nano drone-detection prototype using YOLO and RF/SDR analysis, and an Arduino-based rocket-telemetry project using an IMU and barometric altimeter. These projects reflect my interest in practical work that connects software, hardware, and reliable data collection.'
 elif kind=='Cybersecurity and automation':
  opening=f'I am writing to ask about opportunities in cybersecurity, security assessment, infrastructure, or Python automation at {company}.'
  evidence_text='At Acumen Engineered Solution Inc., I performed vulnerability and risk assessments using Nessus and authored an assessment framework based on NIST SP 800-115. I also built a Python tool that sped up patch-management data gathering by 20%. At IESO, I supported network and security infrastructure and contributed to DMZ firewall cleanup.'
  detail='I hold an M.S. in Information Technology from Arizona State University and enjoy combining hands-on technical work with clear documentation and practical remediation recommendations.'
 elif kind=='Software and cloud automation':
  opening=f'I am reaching out about application development, Python automation, cloud-support, or technical systems opportunities at {company}.'
  evidence_text='During my TELUS internship, I worked on modernizing internal applications and supported development using Node.js, JavaScript, GitHub workflows, and Pulumi in Google Cloud. My experience also includes Python automation and vulnerability assessment at Acumen Engineered Solution Inc., and infrastructure work at IESO.'
  detail='I hold an M.S. in Information Technology from Arizona State University. My personal Python projects include local AI workflows using LangChain and Ollama, with an emphasis on keeping sensitive data on-device.'
 else:
  opening=f'I am reaching out to ask about current or upcoming infrastructure, networking, cybersecurity, or automation opportunities at {company}.'
  evidence_text='I hold an M.S. in Information Technology from Arizona State University. My experience at TELUS, IESO, and Acumen Engineered Solution Inc. spans application modernization, network and security hardware deployment, vulnerability assessment, and Python automation.'
  detail='I have worked with Linux, Cisco switches, Check Point firewalls, and cloud deployment tools. I am interested in a hands-on role where I can combine practical systems work with clear technical documentation and problem-solving.'
 closing='I have attached my résumé. Would you be open to considering me for roles suited to my experience, or directing me to the appropriate hiring contact?'
 return (f'Technical opportunities at {company} — Panth Thakkar',f'{greeting}\n\n{opening}\n\n{evidence_text}\n\n{detail}\n\n{closing}\n\n{signature}',ATTACHMENT)

md=(ROOT/'initial_email_drafts.md').read_text()
sections=re.split(r'\n## ',md)
prepared=[];seen={}
fields=['outreach_category','to_email','to_name','delivery_channel','recipient_source_url','recipient_review_status','draft_subject','draft_body','resume_attachment','follow_up_subject','follow_up_body','draft_review_status','draft_review_notes','duplicate_of','draft_basis']
for index,row in enumerate(rows):
 email,name,url,channel,recipient_status,notes=recipient(row);kind=category(row)
 subject,body,attachment=draft(row,kind,name,channel)
 key=norm(row['member'])
 if key in verified:
  email,url,section_num=verified[key];section=next(x for x in sections if x.startswith(str(section_num)+'. '))
  subject=re.search(r'^Subject: (.*)$',section,re.M).group(1)
  body=section.split('\n\nDear ',1)[1].split('\nRecipient',1)[0]
  body='Dear '+body.rstrip().replace('  \n','\n');attachment=ATTACHMENT
  notes.append('Company-specific draft; recruiting address verified on the official careers page on November 5, 2026.')
 identity=row['homepage_ownership_review_status'].startswith('Needs') or 'identity needs review' in row['headquarters_review_status'] or 'existing website/contact match needs review' in row['headquarters_review_status']
 if key in verified:status='Company-specific draft — review before use'
 elif kind=='Existing AVSS interview':status='Existing interview process — choose your known contact'
 elif kind=='Individual networking':status='Networking draft — recipient not confirmed'
 elif identity:status='Hold — company identity/recipient needs review'
 elif not email:status='Contact-page draft — review channel' if channel=='Contact page' else 'Hold — suitable recipient not confirmed'
 else:status='General inquiry draft — review recipient and purpose'
 # If the company match is uncertain, keep the candidate in the original columns instead of selecting it.
 if identity and key not in verified:
  email='';notes.append('No email selected while the company match remains under review; existing contact candidates are preserved in the original columns.')
 if channel=='Contact page':notes.append('For a web form, adapt the attachment sentence if the form does not support résumé uploads.')
 if kind=='Institutional IT inquiry':notes.append('Target employer HR/IT hiring; student admissions and student career-service addresses are not selected.')
 if email and 'queries' in email:
  attachment='';body=body.replace('I have attached my résumé.','I would be happy to share my résumé.').replace("I've attached my résumé.",'I would be happy to share my résumé.')
  notes.append('Recruitment-query inbox: confirm the appropriate application channel; attachment left optional.')
 duplicate_key=(key,host(row['likely_company_homepage']))
 duplicate=seen.get(duplicate_key,'');seen.setdefault(duplicate_key,f"{row['association']}: {row['member']}")
 if duplicate:notes.append('Duplicate company entry across association lists; select one draft for this company.')
 company=display(row['member'])
 follow_subject='' if kind=='Existing AVSS interview' else 'Re: '+subject
 follow_body='' if kind in ('Existing AVSS interview','Individual networking') else f'Hello {name or company+" Team"},\n\nI am following up on my earlier inquiry about technical opportunities at {company}. I remain interested in a role where I can contribute my infrastructure, security, and automation experience.\n\nI would appreciate any guidance on suitable opportunities or the appropriate hiring contact. I am happy to provide further details about my experience or projects.\n\nThank you for your time.\n\n{SIGNATURE}'
 values=[kind,email,name,channel,url,recipient_status,subject,body,attachment,follow_subject,follow_body,status,' '.join(notes),duplicate,'October 2026 résumé; general opportunity inquiry. No matching vacancy, hiring status, relocation, work authorization, security clearance, or certification validity assumed.']
 prepared.append({'association':row['association'],'member':row['member'],'values':values})
 assert len(values)==15 and not re.search(r'\[(?:Company|Name|Hiring|Role|Contact)',body)
 assert len(body)<32767 and len(follow_body)<32767
 if email:assert '@' in email and email.lower() not in blocked and not exclude.search(email.split('@')[0])

payload={'input_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'input_fields':list(rows[0]),'input_rows':rows,'new_fields':fields,'draft_rows':prepared,'resume_source':str(RESUME),'prepared_date':'2026-11-05'}
(ROOT/'draft_data.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
summary={'entries':len(prepared),'selected_email_recipients':sum(bool(r['values'][1]) for r in prepared),'verified_company_specific_drafts':sum(r['values'][11].startswith('Company-specific') for r in prepared),'contact_page_drafts':sum(r['values'][11].startswith('Contact-page') for r in prepared),'held_for_review':sum(r['values'][11].startswith('Hold') for r in prepared),'existing_avss_interview_entries':sum(r['values'][0]=='Existing AVSS interview' for r in prepared),'duplicates_flagged':sum(bool(r['values'][13]) for r in prepared),'categories':dict(Counter(r['values'][0] for r in prepared)),'statuses':dict(Counter(r['values'][11] for r in prepared))}
(ROOT/'draft_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
