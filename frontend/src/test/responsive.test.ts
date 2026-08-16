import css from '../styles/global.css?raw';
test('critical layouts have tablet and mobile breakpoints',()=>{expect(css).toContain('@media(max-width:900px)');expect(css).toContain('@media(max-width:600px)');expect(css).toContain('.opportunity-list{grid-template-columns:1fr}');expect(css).toContain('nav a{min-height:44px}')});
