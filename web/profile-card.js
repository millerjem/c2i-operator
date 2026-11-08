// Framework-neutral: serve locally and handle events through your authenticated backend.
const style = `
:host{display:block;color:#e9f2ef;font:15px/1.5 system-ui,sans-serif;max-width:560px}
article{background:#0b242c;border:1px solid #376068;border-radius:14px;padding:24px}
h2{font-size:23px;margin:5px 0 12px}p{color:#b8ccca}small{color:#88cbbd}
label{display:block;margin:14px 0 4px}input,select{box-sizing:border-box;width:100%;padding:10px;background:#102f36;color:#e9f2ef;border:1px solid #63858a;border-radius:5px}
button,a{display:inline-block;margin:16px 8px 0 0;padding:9px 13px;border-radius:5px;border:1px solid #74b6ac;background:#194a4b;color:#e9f2ef;cursor:pointer;font:inherit}
a{color:#f7a365;background:transparent;border-color:#916140}button:disabled{opacity:.5;cursor:default}
code{display:block;overflow-wrap:anywhere;font-size:11px;color:#b8ccca}summary{cursor:pointer}dl{font-size:12px}dd{margin:0 0 10px;overflow-wrap:anywhere}
`;
const node = (tag, value) => {const n = document.createElement(tag); if(value !== undefined)n.textContent=String(value);return n;};
export function safeLink(value) {
  try {const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:null;}catch{return null;}
}
export class C2IProfileCard extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});}
  set card(value){this._card=value;this.render();}
  get card(){return this._card;}
  render(){
    const c=this._card;if(!c)return;
    const root=this.shadowRoot;root.replaceChildren();
    const css=node('style',style),article=node('article');root.append(css,article);
    article.append(node('small',`VerteX profile · ${c.profileVersion}`),node('h2',c.title),node('strong',c.phase),node('p',c.message));
    const form=node('form');article.append(form);
    const inputs=[];
    for(const f of c.settings||[]){
      const id=`setting-${inputs.length}`,label=node('label',f.label);label.htmlFor=id;
      const input=node(f.options?.length?'select':'input');input.id=id;input.name=f.name;
      if(f.options?.length){for(const o of f.options){const option=node('option',o.label||o.value);option.value=o.value;input.append(option);}}
      else {input.type='text';if(f.format==='number')input.inputMode='decimal';}
      input.value=String(f.value??'');inputs.push(input);form.append(label,input);
    }
    const request=node('button','Request settings update');request.type='submit';form.append(request);
    form.addEventListener('submit',e=>{e.preventDefault();this.dispatchEvent(new CustomEvent('c2i-request',{bubbles:true,composed:true,detail:{name:c.name,requestUID:c.requestUID,settings:Object.fromEntries(inputs.map(x=>[x.name,x.value]))}}));});
    const details=node('details');details.append(node('summary','Review this plan'));
    const dl=node('dl');for(const [label,value]of Object.entries(c.review||{})){dl.append(node('dt',label),node('dd',typeof value==='object'?JSON.stringify(value):value));}details.append(dl,node('code',c.digest));article.append(details);
    if(c.actions?.approve){const approve=node('button','Approve reviewed plan');approve.type='button';approve.addEventListener('click',()=>this.dispatchEvent(new CustomEvent('c2i-approve',{bubbles:true,composed:true,detail:{name:c.name,requestUID:c.requestUID,planDigest:c.digest}})));article.append(approve);}
    const href=safeLink(c.vertexURL);if(href){const a=node('a','Review in VerteX');a.href=href;a.target='_blank';a.rel='noopener noreferrer';article.append(a);}
  }
}
if(!customElements.get('c2i-profile-card'))customElements.define('c2i-profile-card',C2IProfileCard);
