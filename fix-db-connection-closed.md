# 修复记录：上传 PDF 报 500 "connection is closed"

> 2026-10-19。简单说清楚：出了什么事、为什么、改了哪两处代码、为什么改完就好了。

## 一、出了什么事

上传 PDF（解析耗时 21.4 秒）成功解析后，落库时报错：

```
asyncpg.exceptions.InterfaceError: connection is closed
POST /documents/upload/parse → 500
```

也就是：**解析挺成功，最后写数据库那一步，发现数据库连接已经死了。**

## 二、排查过程（证据链）

1. **看报错位置**：崩在 `db.commit()`，而且 INSERT 是这次请求里解析完之后的**第一条** PG 语句。
2. **查服务端是不是凶手**：进 Postgres 容器查了三个超时（`idle_in_transaction_session_timeout` / `statement_timeout` / `idle_session_timeout`）全是 0（不超时），服务端日志也没有杀连接的记录 → **数据库无辜**。
3. **捋时间线**：请求进来 → 鉴权时跑了几个 SELECT（从连接池**借出**一条连接）→ 然后 PDF 解析 21.4 秒 + RustFS 上传 + 写 Mongo，这条连接全程**空闲挂着没人用** → 回来 commit 时连接已被断开。

结论：连接是在长达 20 多秒的空闲期间，被**网络层**（本机 → Docker 端口转发）断掉的。谁断的不重要，重要的是：**我们不该在长时间不用连接的时候还占着它。**

## 三、根因（打个比方）

连接池就像图书馆：

- `pool_pre_ping=True` 相当于**每次借书前检查一下书是不是完好**；
- 但你借出之后（鉴权用了连接），捧着书去睡了 21 秒（解析 PDF），期间书被人抽走了（网络断开），图书馆是管不着的——**借出后的体检，借出前的 ping 保护不了**。

所以真正的设计问题是：**长耗时操作（解析/上传）期间，一直白占着一条池化连接。**

## 四、改了哪两处

### 改动 1（根因修复）：解析前先把连接还回池子

[service.py](app/modules/documents/service.py) 的 `upload_and_create_document`，在 `file_parser.parse(...)` 之前加了一行：

```python
# 解析可能长达数十秒，期间不占用池化连接（否则空闲连接被网络层断掉后
# commit 报 connection is closed）。close 只归还连接，session 仍可用，
# 后续 create() 会经 pool_pre_ping 重新取一条健康连接。
await db.close()
```

说明：

- `db.close()` **只归还连接**，session 对象没废，后面照样能 `db.add()` / `db.commit()`；
- 下次真正用的时候，SQLAlchemy 会**重新从池里借一条**，借之前 `pool_pre_ping` 会先 ping——死连接直接丢弃换新的，所以 commit 不会再踩到死连接；
- FastAPI 请求结束时的 `session.close()` 是幂等的，重复关没副作用。

### 改动 2（顺带修的次生问题）：PDF 文本提取挪进线程池

[pdf_parser.py](app/modules/documents/parsers/pdf_parser.py) 里，按页提取文本的 `pypdf` 循环原来是**在事件循环里同步跑**的：

```python
# 改前：直接在事件循环里跑，一卡卡几秒
for page in reader.pages:
    page_texts.append((page.extract_text() or "").strip())
```

```python
# 改后：和图片/表格提取一样，扔进线程池
def _extract_texts() -> list[str]:
    ...
page_texts = await asyncio.to_thread(_extract_texts)
```

为什么顺带改它：FastAPI 是单事件循环的，同步 CPU 代码跑多久，**整个服务就卡多久**——期间所有请求、SSE 推流全部冻结。文件里图片提取、表格提取（pdfplumber）本来就在线程池里跑，唯独文本提取漏了，这次补齐。

## 五、一句话总结

> **长耗时操作别占着数据库连接；同步重活别堵事件循环。**
> 前者靠"先 `db.close()` 归还、用时再借（借时自带体检）"，后者靠 `asyncio.to_thread`。

## 六、面试角度（这个坑 worth 讲）

如果被问"FastAPI 异步实践中踩过什么坑"，这题很好用：

1. 现象：异步框架里一个同步解析器把服务卡了 20 秒；
2. 排查：报错在 commit，回溯时间线发现连接空闲 21 秒，查服务端超时排除数据库嫌疑；
3. 修复：归还连接 + `to_thread`，并能讲清 `pool_pre_ping` 的保护边界（只在 checkout 时生效）；
4. 升华：**"异步框架里写同步代码"和"长事务占连接"是两类最常见的事故**，这里一次踩了俩。

---

*验证方式：服务带 `--reload` 自动重载后，重新上传同一个 PDF（01-travel-expense-policy.pdf），应能正常落库返回 documentId。*
