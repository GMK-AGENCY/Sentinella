// Owned sender only. No model call, tools, approval handlers or recipient install.
export function register(on) {
  on('session.start', async ($, e, next) => {
    await $.command.register({name:'sentinella-status',description:'Verifica il collegamento locale'});
    await $.command.register({name:'sentinella-send',description:'Consegna una prenotazione locale'});
    return next(e);
  });
  on('command.run', {command:'sentinella-status'}, async ($) => ({
    text:'SENTINELLA_JSON:'+JSON.stringify({ready:true,sessionId:await $.session.id(),revision:1})
  }));
  on('command.run', {command:'sentinella-send'}, async ($, e) => {
    const p=JSON.parse(e.args);
    const uuid=/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
    if (!uuid.test(p.attempt) || !uuid.test(p.target) || typeof p.text!=='string' || !p.text.trim() || p.text.length>24000)
      throw new Error('Prenotazione non valida');
    const result=await $.session.send({to:{sessionId:p.target},text:p.text});
    return {text:'SENTINELLA_JSON:'+JSON.stringify({attempt:p.attempt,target:p.target,...result})};
  });
}
