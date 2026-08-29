with open("Frontend/careloop-dashboard.html", encoding="utf-8") as f:
    content = f.read()

old = """    if(d.success){
      const c = customers.find(x=>x.id===parseInt(customerId));
      addNotification({type:"cake",title:"Birthday email sent",names:c?c.name:""});
      clAlert("Birthday email sent successfully!","success");"""

new = """    if(d.success){
      const c = customers.find(x=>x.id===parseInt(customerId));
      if(c) c.last_birthday_email_sent = new Date().toISOString();
      addNotification({type:"cake",title:"Birthday email sent",names:c?c.name:""});
      clAlert("Birthday email sent successfully!","success");"""

if old in content:
    content = content.replace(old, new, 1)
    with open("Frontend/careloop-dashboard.html", "w", encoding="utf-8") as f:
        f.write(content)
    print("sendBirthdayEmail now updates in-memory customer record on success")
else:
    print("Pattern not found")
