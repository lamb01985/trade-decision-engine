import css from '../styles/global.css?raw';
test('critical layouts have tablet and mobile breakpoints',()=>{expect(css).toContain('@media(max-width:900px)');expect(css).toContain('@media(max-width:600px)');expect(css).toContain('.opportunity-list{grid-template-columns:1fr}');expect(css).toContain('nav a{min-height:44px}')});
test('mobile prioritizes decision before chart',()=>{expect(css).toContain('.decision-stack{order:1}');expect(css).toContain('.chart-workspace{order:2')});
