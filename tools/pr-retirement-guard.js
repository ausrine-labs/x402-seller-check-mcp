'use strict';

const RECEIPT_HEADER = 'PR RETIREMENT REVIEW v1';
const RECEIPT_MAX_AGE_MS = 15 * 60 * 1000;
const TRUSTED_AUTHOR_ASSOCIATIONS = new Set(['OWNER', 'MEMBER', 'COLLABORATOR']);
// Existing designated operator. Repository membership is not approval authority.
// This authenticates the operator's attestation; human approval is separately
// required by the execution gate and retained in the private review receipt.
const RETIREMENT_APPROVER = Object.freeze({ id: 262888832, login: 'LietuvaAI' });

function receiptMatches(
  comments,
  { headSha, repositoryName, pullNumber, sourcePaths, authorizedActor, authorizedActorId, closedAt },
) {
  const exactHead = `- Exact head: \`${headSha}\``;
  const disposition = /- Disposition: `(superseded|abandoned|rejected)`/;
  const closedAtMs = Date.parse(closedAt);
  if (!authorizedActor || authorizedActorId !== RETIREMENT_APPROVER.id ||
      authorizedActor.toLowerCase() !== RETIREMENT_APPROVER.login.toLowerCase() ||
      !Number.isFinite(closedAtMs)) return false;
  return comments.some((comment) => {
    const body = typeof comment.body === 'string' ? comment.body : '';
    const author = comment.user && comment.user.login;
    const createdAtMs = Date.parse(comment.created_at);
    const receiptAgeMs = closedAtMs - createdAtMs;
    if (
      typeof author !== 'string' ||
      comment.user.id !== RETIREMENT_APPROVER.id ||
      comment.user.type !== 'User' ||
      author.toLowerCase() !== authorizedActor.toLowerCase() ||
      !TRUSTED_AUTHOR_ASSOCIATIONS.has(comment.author_association) ||
      !Number.isFinite(createdAtMs) ||
      receiptAgeMs < 0 ||
      receiptAgeMs > RECEIPT_MAX_AGE_MS ||
      body.split('\n')[0] !== RECEIPT_HEADER ||
      !body.includes(`- Repository: \`${repositoryName}\``) ||
      !body.includes(`- PR: \`#${pullNumber}\``) ||
      !body.includes(exactHead) ||
      !body.includes('- Review summary: ') ||
      !body.includes('- Per-file review:') ||
      !body.includes('- Authorization: `Vilija exact-head approval attested by designated operator`') ||
      !/^- Authorization evidence SHA256: `[a-f0-9]{64}`$/m.test(body) ||
      !body.includes('This receipt records content disposition before exact-head branch retirement.') ||
      !disposition.test(body)
    ) {
      return false;
    }
    const reviewedPaths = [];
    for (const line of body.split('\n')) {
      const match = line.match(/^  - `([^`]+)` → `(preserved_exact|preserved_rewritten|intentionally_dropped)` → `[^`]+`/);
      if (match) reviewedPaths.push(match[1]);
    }
    const expected = [...sourcePaths].sort();
    const recorded = [...new Set(reviewedPaths)].sort();
    return reviewedPaths.length === recorded.length && expected.length === recorded.length &&
      expected.every((path, index) => path === recorded[index]);
  });
}

async function hasValidReceipt(comments, receiptContext, github) {
  if (!github || typeof github.graphql !== 'function') return false;
  for (const comment of comments) {
    if (!receiptMatches([comment], receiptContext) || typeof comment.node_id !== 'string') continue;
    try {
      // A collaborator may be able to edit an operator comment. REST timestamps
      // alone cannot rule out an edit within the creation second.
      const { node } = await github.graphql(`query($id: ID!) {
        node(id: $id) { __typename ... on IssueComment {
          id body lastEditedAt editor { login } author { login }
          repository { nameWithOwner } pullRequest { number }
        }}
      }`, { id: comment.node_id });
      if (node && node.__typename === 'IssueComment' && node.id === comment.node_id &&
          node.body === comment.body && node.lastEditedAt === null && node.editor === null &&
          node.author && node.author.login.toLowerCase() === RETIREMENT_APPROVER.login.toLowerCase() &&
          node.repository && node.repository.nameWithOwner === receiptContext.repositoryName &&
          node.pullRequest && node.pullRequest.number === receiptContext.pullNumber) return true;
    } catch (_) {
      // Missing/unavailable authoritative edit history is not authorization.
    }
  }
  return false;
}

async function guardUnmergedClosure({ github, context, core }) {
  const pull = context.payload.pull_request;
  const repository = context.payload.repository;
  if (!pull || !repository) {
    throw new Error('pull_request and repository payloads are required');
  }
  if (pull.merged) {
    return { action: 'merged-noop' };
  }

  const owner = repository.owner.login;
  const repo = repository.name;
  const pullNumber = pull.number;
  const headSha = pull.head.sha;
  const headRef = pull.head.ref;
  const headRepo = pull.head.repo && pull.head.repo.full_name;
  const repositoryName = repository.full_name;
  const authorizedActor = context.actor || (context.payload.sender && context.payload.sender.login);
  const authorizedActorId = context.payload.sender && context.payload.sender.id;
  const closedAt = pull.closed_at;
  const currentHead = async () => {
    try {
      return (await github.rest.git.getRef({ owner, repo, ref: `heads/${headRef}` })).data.object.sha;
    } catch (error) {
      if (error.status === 404) return null;
      throw error;
    }
  };
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner,
    repo,
    issue_number: pullNumber,
    per_page: 100,
  });
  const files = await github.paginate(github.rest.pulls.listFiles, {
    owner,
    repo,
    pull_number: pullNumber,
    per_page: 100,
  });
  const sourcePaths = files.map((file) => file.filename);

  if (await hasValidReceipt(comments, {
    headSha,
    repositoryName,
    pullNumber,
    sourcePaths,
    authorizedActor,
    authorizedActorId,
    closedAt,
  }, github)) {
    return { action: 'receipted-noop' };
  }

  const explanation = [
    'This unmerged PR was closed without a verified, unedited operator-attested exact-head `PR RETIREMENT REVIEW v1` receipt.',
    '',
    'The governed lifecycle does not treat Close as completion. Review every changed file, then use the canonical `culturenet-brain/tools/retire-pr-branch.py` command with the full head SHA and Vilija’s exact approval.',
  ].join('\n');

  if (headRepo !== repositoryName) {
    await github.rest.issues.createComment({
      owner,
      repo,
      issue_number: pullNumber,
      body: `${explanation}\n\nThe head belongs to external repository \`${headRepo || 'unknown'}\`, so this guard could not safely restore or reopen it. Map Room review is required.`,
    });
    core.setFailed('Unreceipted fork-owned PR closure requires Map Room review.');
    return { action: 'fork-blocked' };
  }

  let ref;
  try {
    const response = await github.rest.git.getRef({ owner, repo, ref: `heads/${headRef}` });
    ref = response.data;
  } catch (error) {
    if (error.status !== 404) {
      throw error;
    }
  }

  if (!ref) {
    await github.rest.git.createRef({
      owner,
      repo,
      ref: `refs/heads/${headRef}`,
      sha: headSha,
    });
  } else if (ref.object.sha !== headSha) {
    await github.rest.issues.createComment({
      owner,
      repo,
      issue_number: pullNumber,
      body: `${explanation}\n\nThe branch now points to \`${ref.object.sha}\`, not the closed PR head \`${headSha}\`. The guard refused to overwrite it. Map Room review is required.`,
    });
    core.setFailed('Unreceipted PR closure found a changed branch head.');
    return { action: 'head-race-blocked' };
  }

  // Check after restoration as well as after reopening. Reopen has no atomic
  // expected-head parameter; a racing writer must be detected, never overwritten
  // or reported as a successfully restored exact head.
  const beforeOpen = await currentHead();
  if (beforeOpen !== headSha) {
    core.setFailed('Branch changed before reopen; no ref was overwritten.');
    await github.rest.issues.createComment({ owner, repo, issue_number: pullNumber,
      body: `${explanation}\n\nThe branch moved before reopen. No ref was overwritten; exact-head recovery requires owner review.` });
    return { action: 'head-race-blocked' };
  }
  await github.rest.pulls.update({ owner, repo, pull_number: pullNumber, state: 'open' });
  const afterOpen = await currentHead();
  const reopened = await github.rest.pulls.get({ owner, repo, pull_number: pullNumber });
  if (afterOpen !== headSha || reopened.data.head.sha !== headSha || reopened.data.state !== 'open') {
    core.setFailed('Head/state changed during reopen; exact-head recovery is unverified.');
    await github.rest.issues.createComment({ owner, repo, issue_number: pullNumber,
      body: `${explanation}\n\nThe head or PR state moved during reopen. No ref was overwritten; the restored exact-head outcome is not verified and requires owner review.` });
    return { action: 'reopen-race-blocked' };
  }
  await github.rest.issues.createComment({
    owner,
    repo,
    issue_number: pullNumber,
    body: `${explanation}\n\nThe exact branch head \`${headSha}\` was ${ref ? 'still present' : 'restored'} and this PR was reopened automatically.`,
  });
  core.setFailed('Unmerged PR closure was reversed because its retirement receipt was missing.');
  return { action: ref ? 'reopened' : 'restored-and-reopened' };
}

module.exports = { guardUnmergedClosure, hasValidReceipt, RETIREMENT_APPROVER };
