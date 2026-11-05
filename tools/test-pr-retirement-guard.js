'use strict';

const assert = require('node:assert/strict');
const { guardUnmergedClosure, hasValidReceipt } = require('./pr-retirement-guard');

const HEAD = '0123456789abcdef0123456789abcdef01234567';
const CLOSED_AT = '2026-09-06T04:00:00Z';
const RECEIPT_CREATED_AT = '2026-09-06T03:59:30Z';

function payload(overrides = {}) {
  return {
    pull_request: {
      number: 17,
      merged: false,
      closed_at: CLOSED_AT,
      head: { sha: HEAD, ref: 'codex/example', repo: { full_name: 'example/project' } },
      ...overrides,
    },
    repository: { name: 'project', full_name: 'example/project', owner: { login: 'example' } },
    sender: { id: 262888832, login: 'LietuvaAI', type: 'User' },
  };
}

function receiptBody(head = HEAD, path = 'docs/example.md') {
  return [
    'PR RETIREMENT REVIEW v1',
    '- Repository: `example/project`',
    '- PR: `#17`',
    `- Exact head: \`${head}\``,
    '- Disposition: `superseded`',
    '- Review summary: The replacement preserves the reviewed content.',
    '- Authorization: `Vilija exact-head approval attested by designated operator`',
    `- Authorization evidence SHA256: \`${'a'.repeat(64)}\``,
    '- Per-file review:',
    `  - \`${path}\` → \`preserved_exact\` → \`${path}\``,
    'This receipt records content disposition before exact-head branch retirement.',
  ].join('\n');
}

function receiptComment(overrides = {}) {
  return {
    body: receiptBody(),
    node_id: 'IC_example_1',
    user: { id: 262888832, login: 'LietuvaAI', type: 'User' },
    author_association: 'OWNER',
    created_at: RECEIPT_CREATED_AT,
    ...overrides,
  };
}

function harness({ comments = [], files = [{ filename: 'docs/example.md' }], ref = null,
  pullOverrides = {}, refHeads = [], graphqlOverride = {}, graphqlFails = false,
  reopenedHead, reopenedState = 'open' } = {}) {
  const calls = [];
  const listComments = Symbol('listComments');
  const listFiles = Symbol('listFiles');
  let reads = 0;
  const github = {
    paginate: async (endpoint) => (endpoint === listComments ? comments : files),
    graphql: async (_query, { id }) => {
      if (graphqlFails) throw new Error('unavailable');
      const comment = comments.find((row) => row.node_id === id);
      return { node: { __typename: 'IssueComment', id, body: comment.body,
        lastEditedAt: null, editor: null, author: { login: comment.user.login },
        repository: { nameWithOwner: 'example/project' }, pullRequest: { number: 17 },
        ...graphqlOverride } };
    },
    rest: {
      issues: {
        listComments,
        createComment: async (args) => calls.push(['comment', args]),
      },
      git: {
        getRef: async () => {
          if (reads < refHeads.length) {
            const sha = refHeads[reads];
            ref = sha ? { object: { sha } } : null;
          }
          reads += 1;
          if (!ref) {
            const error = new Error('not found');
            error.status = 404;
            throw error;
          }
          return { data: ref };
        },
        createRef: async (args) => { calls.push(['createRef', args]); ref = { object: { sha: args.sha } }; },
      },
      pulls: {
        listFiles,
        update: async (args) => calls.push(['updatePull', args]),
        get: async () => ({ data: { state: reopenedState, head: { sha: reopenedHead || ref.object.sha } } }),
      },
    },
  };
  const failures = [];
  const core = { setFailed: (message) => failures.push(message) };
  return {
    github,
    context: { actor: 'LietuvaAI', payload: payload(pullOverrides) },
    core,
    calls,
    failures,
  };
}

const receiptValid = (comments, context, options = {}) =>
  hasValidReceipt(comments, context, harness({ comments, ...options }).github);

async function main() {
  const receiptContext = {
    headSha: HEAD,
    repositoryName: 'example/project',
    pullNumber: 17,
    sourcePaths: ['docs/example.md'],
    authorizedActor: 'LietuvaAI',
    authorizedActorId: 262888832,
    closedAt: CLOSED_AT,
  };
  assert.equal(await receiptValid([], receiptContext), false);
  assert.equal(
    await receiptValid([receiptComment()], receiptContext),
    true,
  );
  assert.equal(
    await receiptValid([receiptComment({ body: receiptBody('f'.repeat(40)) })], receiptContext),
    false,
  );
  assert.equal(
    await receiptValid([receiptComment({ body: receiptBody(HEAD, 'wrong.md') })], receiptContext),
    false,
  );
  assert.equal(
    await receiptValid([receiptComment({ user: { login: 'outsider' } })], receiptContext),
    false,
  );
  assert.equal(
    await receiptValid([receiptComment({ author_association: 'NONE' })], receiptContext),
    false,
  );
  assert.equal(
    await receiptValid([receiptComment({ created_at: '2026-09-06T03:30:00Z' })], receiptContext),
    false,
  );
  assert.equal(
    await receiptValid([receiptComment({ created_at: '2026-09-06T04:00:01Z' })], receiptContext),
    false,
  );

  // Membership and a matching closer no longer manufacture approval authority.
  const forged = receiptComment({ user: { id: 42, login: 'collaborator', type: 'User' }, author_association: 'COLLABORATOR' });
  assert.equal(await receiptValid([forged], { ...receiptContext, authorizedActor: 'collaborator', authorizedActorId: 42 }), false);
  assert.equal(await receiptValid([receiptComment({ body: receiptBody().replace(/^- Authorization.*\n/gm, '') })], receiptContext), false);
  assert.equal(await hasValidReceipt([receiptComment()], receiptContext), false);
  for (const options of [
    { graphqlOverride: { lastEditedAt: RECEIPT_CREATED_AT } }, // same-second edit
    { graphqlOverride: { editor: { login: 'collaborator' } } },
    { graphqlOverride: { body: receiptBody() + '\nchanged' } },
    { graphqlOverride: { repository: { nameWithOwner: 'other/repo' } } },
    { graphqlOverride: { pullRequest: { number: 99 } } },
    { graphqlOverride: { pullRequest: null } },
    { graphqlFails: true },
  ]) assert.equal(await receiptValid([receiptComment()], receiptContext, options), false);

  const merged = harness({ pullOverrides: { merged: true } });
  assert.deepEqual(await guardUnmergedClosure(merged), { action: 'merged-noop' });
  assert.deepEqual(merged.calls, []);

  const receipted = harness({
    comments: [receiptComment()],
  });
  assert.deepEqual(await guardUnmergedClosure(receipted), { action: 'receipted-noop' });
  assert.deepEqual(receipted.calls, []);

  const missing = harness();
  assert.deepEqual(await guardUnmergedClosure(missing), { action: 'restored-and-reopened' });
  assert.deepEqual(missing.calls.map(([name]) => name), ['createRef', 'updatePull', 'comment']);
  assert.equal(missing.calls[0][1].sha, HEAD);
  assert.equal(missing.failures.length, 1);

  const present = harness({ ref: { object: { sha: HEAD } } });
  assert.deepEqual(await guardUnmergedClosure(present), { action: 'reopened' });
  assert.deepEqual(present.calls.map(([name]) => name), ['updatePull', 'comment']);

  const raced = harness({ ref: { object: { sha: 'f'.repeat(40) } } });
  assert.deepEqual(await guardUnmergedClosure(raced), { action: 'head-race-blocked' });
  assert.deepEqual(raced.calls.map(([name]) => name), ['comment']);
  assert.equal(raced.failures.length, 1);

  for (const refHeads of [[HEAD, 'f'.repeat(40)], [HEAD, null], [null, 'f'.repeat(40)]]) {
    const before = harness({ refHeads });
    assert.deepEqual(await guardUnmergedClosure(before), { action: 'head-race-blocked' });
    assert.ok(!before.calls.some(([name]) => name === 'updatePull'));
    assert.equal(before.failures.length, 1);
  }
  for (const options of [
    { ref: { object: { sha: HEAD } }, refHeads: [HEAD, HEAD, 'f'.repeat(40)] },
    { ref: { object: { sha: HEAD } }, reopenedHead: 'f'.repeat(40) },
    { ref: { object: { sha: HEAD } }, reopenedState: 'closed' },
  ]) {
    const during = harness(options);
    assert.deepEqual(await guardUnmergedClosure(during), { action: 'reopen-race-blocked' });
    assert.ok(!during.calls.some(([name]) => name === 'createRef'));
    assert.equal(during.failures.length, 1);
  }

  console.log('PASS: unmerged PR guard validates receipts, restores exact heads, reopens, and fails closed on races');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
