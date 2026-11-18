# 就业咨询智能体关联库表数据字典
> - 采集方式：只读账号经 information_schema 读取字段名/类型/注释，行数为 `COUNT(*)` 实计（2026-11-11）
> - 覆盖范围：`gt_lyjy`（聊易就业）与 `gt_sccy`（水城创业网）中与「就业咨询智能体」四类需求相关的表；未列出的其余表与智能体无直接关联
> - 凭据：连接信息在采集端本地配置文件，不入库不入档
> - **客户官方字段说明**（2026-11-17 客户提供，[附件](files/employment-db-field-spec-20261117.xlsx)，单 sheet 6438 行）：与本字典的字段注释核对一致；关键枚举官方口径——`qs_jobs.close_type`：1-手动关闭 2-到期自动关闭；`qs_jobs.audit`/`qs_guide.audit`/`qs_article.audit`：0-未审核 1-审核通过 2-审核未通过（同步侧按 audit=1 过滤，测试库文章全 0 未审核故不入库为正确行为）；`qs_jobs.display`/`is_display`：是否显示

## gt_lyjy（聊易就业平台）

### 岗位与企业（职业介绍咨询知识源）

| 表名 | 说明 | 行数 | 字段数 |
|---|---|---|---|
| `qs_jobs` | 职位表 | 10 | 80 |
| `qs_jobs_contact` | 职位联系方式表 | 5 | 15 |
| `qs_jobs_tag` | 职位标签表 | 5 | 4 |
| `qs_company_profile` | 企业信息表 | 10 | 84 |
| `qs_category_jobs` | 职位分类表 | 1046 | 13 |
| `qs_category_district` | 地区分类表 | 9960 | 7 |
| `qs_category_major` | 专业分类表 | 877 | 4 |
| `qs_industries` | 行业对应一二三产业表 | 114 | 3 |
| `qs_sdgwgj` | 岗位归集数据 | 0 | 11 |
| `gj_sdgwgj` | 本地岗位归集导入数据 | 0 | 45 |
| `qs_gwgj_push` | 山东岗位归集推送数据记录 | 0 | 48 |
| `qs_jyzx` |  | 5 | 6 |
| `qs_jyzx_resume` |  | 5 | 43 |

#### `qs_jobs` 职位表（10 行 · 80 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `uid` | int |  |
| `admin_id` | int | 基层人员ID |
| `type` | tinyint | 职位类型 1企业填写 2基层人员填写 3.紧缺职业 |
| `jobs_name` | varchar(50) |  |
| `companyname` | varchar(50) |  |
| `trade_cn` | varchar(30) |  |
| `district_cn` | varchar(100) |  |
| `tag_cn` | varchar(100) |  |
| `text` | varchar(255) | 失败原因 |
| `jobscode` | varchar(255) | 紧缺职位编码 |
| `jobslevel` | varchar(255) | 技能等级 |
| `company_id` | int unsigned |  |
| `company_addtime` | int unsigned |  |
| `company_audit` | tinyint unsigned |  |
| `emergency` | tinyint unsigned |  |
| `stick` | tinyint |  |
| `nature` | int unsigned |  |
| `nature_cn` | varchar(30) |  |
| `sex` | tinyint unsigned |  |
| `sex_cn` | varchar(3) |  |
| `age` | varchar(10) |  |
| `amount` | smallint unsigned |  |
| `topclass` | smallint unsigned |  |
| `category` | smallint unsigned |  |
| `subclass` | smallint unsigned |  |
| `category_cn` | varchar(100) |  |
| `trade` | smallint unsigned |  |
| `major_ids` | varchar(100) | 专业要求 |
| `skill` | varchar(10) | 职业技能等级 |
| `focus_group` | tinyint | 是否优先招用重点群体 0否 1 是 |
| `language` | varchar(10) | 语言要求 |
| `jobAsk` | varchar(10) | 工作要求 |
| `is_ex_serviceman` | varchar(10) | 是否招收退役军人 |
| `is_disabled` | varchar(10) | 是否招收残疾人 |
| `disabilityType` | varchar(10) | 残疾类别 |
| `disability` | varchar(10) | 残疾等级 |
| `scale` | smallint unsigned |  |
| `scale_cn` | varchar(30) |  |
| `district` | varchar(100) |  |
| `tag` | varchar(50) |  |
| `education` | smallint unsigned |  |
| `education_cn` | varchar(30) |  |
| `experience` | smallint unsigned |  |
| `experience_cn` | varchar(30) |  |
| `minwage` | int |  |
| `maxwage` | int |  |
| `negotiable` | tinyint unsigned |  |
| `contents` | text |  |
| `addtime` | int unsigned |  |
| `deadline` | int unsigned |  |
| `refreshtime` | int unsigned |  |
| `stime` | int |  |
| `setmeal_deadline` | int unsigned |  |
| `setmeal_id` | smallint unsigned |  |
| `setmeal_name` | varchar(60) |  |
| `audit` | tinyint |  |
| `display` | tinyint unsigned |  |
| `click` | int unsigned |  |
| `user_status` | tinyint unsigned |  |
| `robot` | tinyint unsigned |  |
| `tpl` | varchar(60) |  |
| `map_x` | decimal(9,6) |  |
| `map_y` | decimal(9,6) |  |
| `map_zoom` | tinyint unsigned |  |
| `add_mode` | tinyint unsigned |  |
| `department` | varchar(60) |  |
| `famous` | tinyint |  |
| `key_precise` | text |  |
| `key_full` | text |  |
| `like_num` | int | 点赞数量 |
| `subsite_id` | int unsigned |  |
| `share_allowance` | tinyint unsigned |  |
| `jyzx_status` | int |  |
| `gx_id` | int |  |
| `is_gx` | int |  |
| `cert` | varchar(64) | 职位所需证件 |
| `term` | int unsigned | 招聘周期(天) |
| `close_type` | tinyint | 关闭类型 1-手动关闭 2-到期自动关闭 |
| `sdzpw` | int | 3.待对接 |

#### `qs_jobs_contact` 职位联系方式表（5 行 · 15 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `pid` | int unsigned |  |
| `contact` | varchar(80) |  |
| `qq` | varchar(20) |  |
| `telephone` | varchar(80) |  |
| `landline_tel` | varchar(50) |  |
| `address` | varchar(80) |  |
| `email` | varchar(80) |  |
| `notify` | tinyint unsigned |  |
| `notify_mobile` | tinyint |  |
| `contact_show` | tinyint unsigned |  |
| `telephone_show` | tinyint unsigned |  |
| `email_show` | tinyint unsigned |  |
| `qq_show` | tinyint unsigned |  |
| `landline_tel_show` | tinyint |  |

#### `qs_jobs_tag` 职位标签表（5 行 · 4 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `uid` | int unsigned |  |
| `pid` | int unsigned |  |
| `tag` | int unsigned |  |

#### `qs_company_profile` 企业信息表（10 行 · 84 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `uid` | int unsigned |  |
| `tpl` | varchar(60) |  |
| `companyname` | varchar(60) |  |
| `nature` | smallint unsigned |  |
| `nature_cn` | varchar(30) |  |
| `trade` | smallint unsigned |  |
| `trade_cn` | varchar(30) |  |
| `district` | varchar(100) |  |
| `district_cn` | varchar(100) |  |
| `street` | smallint unsigned |  |
| `street_cn` | varchar(50) |  |
| `scale` | smallint unsigned |  |
| `scale_cn` | varchar(30) |  |
| `registered` | varchar(150) |  |
| `currency` | varchar(10) |  |
| `address` | varchar(250) |  |
| `contact` | varchar(100) |  |
| `telephone` | varchar(130) |  |
| `landline_tel` | varchar(50) |  |
| `email` | varchar(100) |  |
| `website` | varchar(100) |  |
| `certificate_img` | varchar(255) |  |
| `logo` | varchar(255) |  |
| `contents` | text |  |
| `setmeal_id` | smallint unsigned |  |
| `setmeal_name` | varchar(30) |  |
| `audit` | tinyint unsigned |  |
| `map_open` | tinyint unsigned |  |
| `map_x` | decimal(9,6) |  |
| `map_y` | decimal(9,6) |  |
| `map_zoom` | tinyint unsigned |  |
| `addtime` | int unsigned |  |
| `refreshtime` | int unsigned |  |
| `click` | int unsigned |  |
| `user_status` | tinyint unsigned |  |
| `contact_show` | tinyint unsigned |  |
| `telephone_show` | tinyint unsigned |  |
| `email_show` | tinyint unsigned |  |
| `landline_tel_show` | tinyint |  |
| `robot` | tinyint unsigned |  |
| `comment` | int unsigned |  |
| `resume_processing` | tinyint |  |
| `tag` | varchar(60) |  |
| `wzp_tpl` | tinyint |  |
| `jobs` | int unsigned |  |
| `replys` | int unsigned |  |
| `famous` | tinyint unsigned |  |
| `qq` | varchar(15) |  |
| `short_name` | varchar(60) |  |
| `short_desc` | varchar(255) |  |
| `video` | varchar(255) |  |
| `video_addtime` | int |  |
| `video_img` | varchar(255) | 视频封面 |
| `video_audit` | int |  |
| `com_report` | tinyint |  |
| `jyzx_status` | int | 就业在线同步状态 |
| `jyzx_msg` | varchar(255) | 返回状态 |
| `zmimg1` | varchar(255) |  |
| `zmimg2` | varchar(255) |  |
| `human_dianziquan` | int | 人力服务机构标志位1人力服务机构 |
| `qualifications_img` | varchar(255) | 人力资源许可证图片 |
| `labor_img` | varchar(255) | 劳务派遣许可证图片 |
| `labor_num` | varchar(64) | 劳务派遣经营许可证编号 |
| `license_img` | varchar(255) | 办学许可证或民办非企业单位登记证书 |
| `license_num` | varchar(255) | 办学许可证编号 |
| `license_str` | varchar(255) | 办学许可证 许可/业务范围 |
| `lianmengchengyuan` | int | 联盟成员 |
| `human_error` | varchar(255) | 申请失败原因 |
| `human_time` | int | 申请时间 |
| `human_admin` | varchar(64) | 申请人力资源服务机构审核人 |
| `human_review_time` | int | 审核时间 |
| `human_score` | decimal(10,1) | 电子券评分（平均值） |
| `human_comidcard` | varchar(255) | 统一社会信用代码 |
| `human_comidnum` | varchar(255) | 人力资源服务证书号 |
| `human_comtype` | varchar(255) | 人力资源服务范围 |
| `is_gx` | int |  |
| `gx_id` | int |  |
| `saveauth` | varchar(255) |  |
| `saveauthid` | int |  |
| `company_idcard` | varchar(255) | 社会统一信用代码 |
| `createTime` | varchar(100) |  |
| `sdzpw` | int | 推送到山东公共招聘网 0.未推送 1.推送成功 2.推送失败 3.待推送 |
| `guiji_show` | tinyint | 是否为岗位归集企业 0展示 1不展示 |

#### `qs_category_jobs` 职位分类表（1046 行 · 13 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `parentid` | smallint unsigned |  |
| `categoryname` | varchar(80) |  |
| `category_order` | smallint unsigned |  |
| `stat_jobs` | varchar(15) |  |
| `stat_resume` | varchar(15) |  |
| `content` | text |  |
| `spell` | varchar(200) |  |
| `relation1` | varchar(30) |  |
| `relation1_cn` | varchar(30) |  |
| `relation2` | varchar(30) |  |
| `relation2_cn` | varchar(30) |  |
| `jobs_tpl` | text |  |

#### `qs_category_district` 地区分类表（9960 行 · 7 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `parentid` | int unsigned |  |
| `categoryname` | varchar(30) |  |
| `category_order` | smallint unsigned |  |
| `stat_jobs` | varchar(15) |  |
| `stat_resume` | varchar(15) |  |
| `spell` | varchar(200) |  |

#### `qs_category_major` 专业分类表（877 行 · 4 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `parentid` | int |  |
| `categoryname` | varchar(50) |  |
| `category_order` | int |  |

#### `qs_industries` 行业对应一二三产业表（114 行 · 3 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `industries_name` | varchar(20) |  |
| `type` | int |  |
| `new` | varchar(255) |  |

#### `qs_sdgwgj` 岗位归集数据（0 行 · 11 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `jobsname` | varchar(255) | 职位名称 |
| `companyname` | varchar(255) | 公司名称 |
| `jobsid` | int | 职位Id |
| `companyid` | int | 公司ID |
| `district` | int | 地区ID |
| `district_cn` | varchar(255) | 地区 |
| `time` | int | 推送时间 |
| `amount` | int | 招聘人数 |
| `refreshtime_cn` | varchar(255) | 更新时间 |
| `refreshtime` | int | 刷新时间 |

#### `gj_sdgwgj` 本地岗位归集导入数据（0 行 · 45 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `aab998` | varchar(255) | 社会统一信用代码 |
| `aab004` | varchar(255) | 单位名称 |
| `aab019` | varchar(255) | 单位类型 |
| `aab022` | varchar(255) | 所属行业 |
| `aab302dw` | varchar(255) | 单位所属地区名称 |
| `aab048` | varchar(255) | 成立日期 |
| `aab049` | varchar(255) | 注册资金（单位：万元 |
| `aab056` | varchar(255) | 人员规模 |
| `aab092` | text | 单位简介 |
| `aae159` | varchar(255) | 电子信箱 |
| `aae005` | varchar(255) | 联系电话 |
| `aae004` | varchar(255) | 联系人 |
| `aae006` | varchar(255) | 通讯地址 |
| `acb217` | varchar(255) | 职位名称 |
| `acb216` | text | 职位描述 |
| `acb240` | varchar(255) | 招聘人数 |
| `aab302po` | varchar(255) | 工作地区名称 |
| `aac010` | varchar(255) | 详细地点 |
| `aae398` | varchar(255) | 到期日期 |
| `acb219` | varchar(255) | 工作性质 |
| `acb212` | varchar(255) | 薪酬类型 |
| `acb214` | varchar(255) | 薪酬上限 |
| `acb213` | varchar(255) | 薪酬下限 |
| `bac011` | varchar(255) | 最低学历 |
| `acb21a` | varchar(255) | 工作年限 |
| `acc01g` | varchar(255) | 专业要求 |
| `aac015` | varchar(255) | 职业技能等级 |
| `acb21b` | varchar(255) | 是否年龄不限 |
| `bcb21b` | varchar(255) | 最低年龄 |
| `ccb21b` | varchar(255) | 最高年龄 |
| `acb21d` | varchar(255) | 语言要求 |
| `acb21x` | varchar(255) | 是否招收残疾人 |
| `acb21x1` | varchar(255) | 残疾类别 |
| `acb21x2` | varchar(255) | 残疾等级 |
| `acb21y` | varchar(255) | 工作要求 |
| `bcb21d` | varchar(255) | 是否招收退役军人 |
| `aab301dw` | varchar(255) | 单位所属地区行政区划代码 |
| `aca112s` | varchar(255) | 职位类别名称 |
| `aab301po` | varchar(255) | 工作地区代码 |
| `aca111s` | varchar(255) | 职位类别代码 |
| `status` | int | 1待修改 2.修改失败 3.待推送 4.推送失败 5.推送成功 |
| `error` | varchar(255) | 未修改成功原因 |
| `address` | varchar(255) | 地区 各县市区统计用 |
| `addtime` | varchar(50) | 整理时间 |

#### `qs_gwgj_push` 山东岗位归集推送数据记录（0 行 · 48 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `acb210` | varchar(255) | 返回唯一标识 |
| `jobsid` | int | 推送职位ID |
| `create_time` | datetime | 推送时间 |
| `district` | varchar(255) | 地区 |
| `company` | varchar(255) | 企业名称 |
| `jobsname` | varchar(255) | 职位名称 |
| `dst` | varchar(255) | 地区ID |
| `aab998` | varchar(255) | 社会统一信用代码 |
| `aab004` | varchar(255) | 单位名称 |
| `aab019` | varchar(255) | 单位类型 |
| `aab022` | varchar(255) | 所属行业 |
| `aab302dw` | varchar(255) | 单位所属地区名称 |
| `aab048` | varchar(255) | 成立日期 |
| `aab049` | varchar(255) | 注册资金 |
| `aab056` | varchar(255) | 人员规模 |
| `aab092` | text | 单位简介 |
| `aae159` | varchar(255) | 电子信箱 |
| `aae005` | varchar(255) | 联系电话 |
| `aae004` | varchar(255) | 联系人 |
| `aae006` | varchar(255) | 通讯地址 |
| `acb217` | varchar(255) | 职位名称 |
| `acb216` | text | 职位描述 |
| `acb240` | varchar(255) | 招聘人数 |
| `aab302po` | varchar(255) | 工作地区名称 |
| `aac010` | varchar(255) | 详细地点 |
| `aae398` | varchar(255) | 到期日期 |
| `acb219` | varchar(255) | 工作性质 |
| `acb212` | varchar(255) | 薪酬类型 |
| `acb214` | varchar(255) | 薪酬上限 |
| `acb213` | varchar(255) | 薪酬下限 |
| `bac011` | varchar(255) | 最低学历 |
| `acb21a` | varchar(255) | 工作年限 |
| `acc01g` | varchar(255) | 专业要求 |
| `aac015` | varchar(255) | 职业技能等级 |
| `acb21b` | varchar(255) | 是否年龄不限 |
| `bcb21b` | varchar(255) | 最低年龄 |
| `ccb21b` | varchar(255) | 最高年龄 |
| `acb21d` | varchar(255) | 语言要求 |
| `acb21x` | varchar(255) | 是否招收残疾人 |
| `acb21x1` | varchar(255) | 残疾类别 |
| `acb21x2` | varchar(255) | 残疾等级 |
| `acb21y` | varchar(255) | 工作要求 |
| `bcb21d` | varchar(255) | 是否招收退役军人 |
| `aab301dw` | varchar(255) | 单位所属地区行政区划代码 |
| `aca112s` | varchar(255) | 职位类别名称 |
| `aab301po` | varchar(255) | 工作地区代码 |
| `aca111s` | varchar(255) | 职位类别代码 |

#### `qs_jyzx`（5 行 · 6 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `zpw_field` | varchar(50) |  |
| `jyzx_field` | varchar(50) |  |
| `zwdl` | varchar(50) | 企业行业code |
| `zwdlpid` | varchar(50) | 父ID |
| `job_code` | varchar(15) | 职位中类code |
| `job_field` | varchar(100) | 职位中类 |

#### `qs_jyzx_resume`（5 行 · 43 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `cvId` | bigint |  |
| `jdId` | int |  |
| `name` | varchar(255) |  |
| `sex` | varchar(255) |  |
| `phoneNumber` | int |  |
| `email` | varchar(255) |  |
| `headUrl` | varchar(255) |  |
| `firstJobTime` | varchar(255) |  |
| `birthday` | varchar(255) |  |
| `workExperience` | varchar(255) |  |
| `provinceName` | varchar(255) |  |
| `cityName` | varchar(255) |  |
| `areaName` | varchar(255) |  |
| `provinceCode` | varchar(255) |  |
| `cityCode` | varchar(255) |  |
| `areaCode` | varchar(255) |  |
| `address` | varchar(255) |  |
| `selfDescribe` | varchar(255) |  |
| `workStatus` | varchar(255) |  |
| `realName` | varchar(255) |  |
| `cardNumber` | varchar(255) |  |
| `position` | varchar(255) |  |
| `workSiteProvince` | varchar(255) |  |
| `workSiteCity` | varchar(255) |  |
| `lowSalary` | decimal(10,2) |  |
| `highSalary` | decimal(10,2) |  |
| `companyName` | varchar(255) |  |
| `companyVerification` | varchar(255) |  |
| `positionName` | varchar(255) |  |
| `workStartDate` | varchar(255) |  |
| `workEndDate` | varchar(255) |  |
| `workContent` | varchar(255) |  |
| `schoolName` | varchar(255) |  |
| `eduVerification` | varchar(255) |  |
| `majorName` | varchar(255) |  |
| `degree` | varchar(255) |  |
| `fullTime` | varchar(255) |  |
| `eduSrartDate` | varchar(255) |  |
| `eduEndDate` | varchar(255) |  |
| `certificateName` | varchar(255) |  |
| `certificateNumber` | varchar(255) |  |
| `certificateUrl` | varchar(255) |  |

### 政策与就业指导内容（政策/指导咨询知识源）

| 表名 | 说明 | 行数 | 字段数 |
|---|---|---|---|
| `qs_guide` | 就业指导表 | 58 | 24 |
| `qs_guide_category` | 就业指导分类表 | 7 | 4 |
| `qs_article` | 资讯表 | 10 | 25 |
| `qs_article_category` | 资讯分类表 | 23 | 8 |
| `qs_notice` |  | 5 | 15 |
| `qs_explain` |  | 8 | 14 |
| `qs_help` |  | 0 | 8 |
| `qs_help_category` |  | 30 | 4 |

#### `qs_guide` 就业指导表（58 行 · 24 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `type_id` | smallint unsigned | 分类id |
| `title` | varchar(100) |  |
| `content` | mediumtext |  |
| `tit_color` | varchar(10) |  |
| `tit_b` | tinyint unsigned |  |
| `small_img` | varchar(255) |  |
| `author` | varchar(50) |  |
| `source` | varchar(100) |  |
| `focos` | tinyint unsigned |  |
| `is_display` | tinyint unsigned |  |
| `is_url` | varchar(200) |  |
| `seo_keywords` | varchar(100) |  |
| `seo_description` | varchar(200) |  |
| `click` | int unsigned |  |
| `addtime` | int unsigned |  |
| `guide_order` | smallint unsigned |  |
| `robot` | tinyint unsigned |  |
| `subsite_id` | int unsigned |  |
| `admin_id` | int | 发布人ID |
| `admin_name` | varchar(32) | 发布人 |
| `audit` | tinyint | 审核状态 0-未审核 1-审核通过 2-审核未通过 |
| `audit_admin` | varchar(32) | 审核人 |
| `byte_display` | tinyint | 字节小程序是否显示 |

#### `qs_guide_category` 就业指导分类表（7 行 · 4 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | smallint unsigned |  |
| `categoryname` | varchar(30) |  |
| `category_order` | smallint unsigned |  |
| `admin_set` | tinyint unsigned |  |

#### `qs_article` 资讯表（10 行 · 25 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `type_id` | smallint unsigned |  |
| `parentid` | smallint unsigned |  |
| `title` | varchar(100) |  |
| `content` | mediumtext |  |
| `tit_color` | varchar(10) |  |
| `tit_b` | tinyint unsigned |  |
| `small_img` | varchar(255) |  |
| `author` | varchar(50) |  |
| `source` | varchar(100) |  |
| `focos` | tinyint unsigned |  |
| `is_display` | tinyint unsigned |  |
| `is_url` | varchar(200) |  |
| `seo_keywords` | varchar(100) |  |
| `seo_description` | varchar(200) |  |
| `click` | int unsigned |  |
| `addtime` | int unsigned |  |
| `article_order` | smallint unsigned |  |
| `robot` | tinyint unsigned |  |
| `subsite_id` | int unsigned |  |
| `admin_id` | int | 发布人ID |
| `admin_name` | varchar(32) | 发布人 |
| `audit` | tinyint | 审核状态 0-未审核 1-审核通过 2-审核未通过 |
| `audit_admin` | varchar(32) | 审核人 |
| `byte_display` | tinyint | 字节小程序是否显示 |

#### `qs_article_category` 资讯分类表（23 行 · 8 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | smallint unsigned |  |
| `parentid` | smallint unsigned |  |
| `categoryname` | varchar(80) |  |
| `category_order` | smallint unsigned |  |
| `title` | varchar(255) |  |
| `description` | varchar(255) |  |
| `keywords` | varchar(255) |  |
| `admin_set` | tinyint unsigned |  |

#### `qs_notice`（5 行 · 15 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `type_id` | smallint unsigned |  |
| `title` | varchar(100) |  |
| `content` | mediumtext |  |
| `tit_color` | varchar(10) |  |
| `tit_b` | tinyint |  |
| `is_display` | tinyint unsigned |  |
| `is_url` | varchar(200) |  |
| `seo_keywords` | varchar(100) |  |
| `seo_description` | varchar(200) |  |
| `click` | int |  |
| `addtime` | int |  |
| `sort` | smallint |  |
| `subsite_id` | int unsigned |  |
| `byte_display` | tinyint | 字节小程序是否显示 |

#### `qs_explain`（8 行 · 14 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `type_id` | smallint unsigned |  |
| `title` | varchar(100) |  |
| `content` | mediumtext |  |
| `tit_color` | varchar(10) |  |
| `tit_b` | tinyint |  |
| `is_display` | tinyint unsigned |  |
| `is_url` | varchar(200) |  |
| `seo_keywords` | varchar(100) |  |
| `seo_description` | varchar(200) |  |
| `click` | int |  |
| `addtime` | int |  |
| `show_order` | smallint |  |
| `subsite_id` | int unsigned |  |

#### `qs_help`（0 行 · 8 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `type_id` | smallint unsigned |  |
| `parentid` | smallint unsigned |  |
| `title` | varchar(100) |  |
| `content` | text |  |
| `click` | int unsigned |  |
| `addtime` | int unsigned |  |
| `ordid` | int unsigned |  |

#### `qs_help_category`（30 行 · 4 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | smallint unsigned |  |
| `parentid` | smallint unsigned |  |
| `categoryname` | varchar(80) |  |
| `category_order` | smallint unsigned |  |

### 用户画像与职业意向（意向捕捉与画像回流）

| 表名 | 说明 | 行数 | 字段数 |
|---|---|---|---|
| `qs_resume` | 简历表 | 10 | 100 |
| `qs_resume_education` | 简历教育经历表 | 5 | 15 |
| `qs_resume_work` | 简历工作经历表 | 5 | 12 |
| `qs_resume_training` | 简历培训经历 | 5 | 12 |
| `qs_resume_language` | 简历语言能力表 | 5 | 8 |
| `qs_resume_credent` | 简历证书表 | 5 | 8 |
| `qs_resume_project` | 简历项目经历表 | 5 | 12 |
| `qs_members` | 注册成员表 | 10 | 41 |
| `qs_personal_jobs_apply` | 个人申请职位表 | 10 | 20 |
| `qs_personal_favorites` | 收藏职位表 | 5 | 5 |
| `qs_personal_jobs_subscribe` |  | 4 | 16 |
| `qs_view_jobs` | 职位查看记录表 | 0 | 5 |
| `qs_view_resume` | 简历查看记录表 | 0 | 5 |
| `qs_register_employment` | 灵活就业人员登记 | 5 | 32 |
| `qs_register_employment_personal` | 个体经营就业人员登记 | 5 | 23 |
| `qs_register_unemployment` | 失业登记 | 5 | 35 |

#### `qs_resume` 简历表（10 行 · 100 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `uid` | int unsigned |  |
| `display` | tinyint unsigned |  |
| `display_name` | tinyint unsigned |  |
| `audit` | tinyint unsigned |  |
| `stick` | tinyint unsigned |  |
| `strong_tag` | int unsigned |  |
| `title` | varchar(80) |  |
| `fullname` | varchar(15) |  |
| `sex` | tinyint unsigned |  |
| `sex_cn` | varchar(3) |  |
| `nature` | int unsigned |  |
| `nature_cn` | varchar(30) |  |
| `trade` | varchar(60) |  |
| `trade_cn` | varchar(100) |  |
| `birthdate` | smallint unsigned |  |
| `residence` | varchar(30) |  |
| `height` | varchar(5) |  |
| `marriage` | int unsigned |  |
| `marriage_cn` | varchar(5) |  |
| `experience` | smallint |  |
| `experience_cn` | varchar(30) |  |
| `district` | varchar(100) |  |
| `district_cn` | varchar(255) |  |
| `wage` | smallint unsigned |  |
| `wage_cn` | varchar(30) |  |
| `householdaddress` | varchar(30) |  |
| `education` | smallint unsigned |  |
| `education_cn` | varchar(30) |  |
| `major` | smallint |  |
| `major_cn` | varchar(50) |  |
| `tag` | varchar(50) |  |
| `tag_cn` | varchar(100) |  |
| `telephone` | varchar(50) |  |
| `email` | varchar(60) |  |
| `email_notify` | tinyint unsigned |  |
| `intention_jobs_id` | varchar(100) |  |
| `intention_jobs` | varchar(255) |  |
| `specialty` | varchar(1000) |  |
| `photo` | tinyint unsigned |  |
| `photo_img` | varchar(255) |  |
| `photo_audit` | tinyint unsigned |  |
| `photo_display` | tinyint unsigned |  |
| `qq` | varchar(30) |  |
| `weixin` | varchar(30) |  |
| `addtime` | int unsigned |  |
| `refreshtime` | int unsigned |  |
| `stime` | int |  |
| `entrust` | tinyint unsigned |  |
| `talent` | tinyint unsigned |  |
| `level` | tinyint unsigned |  |
| `complete_percent` | tinyint unsigned | 简历完善度 |
| `current` | smallint unsigned |  |
| `current_cn` | varchar(50) |  |
| `word_resume` | varchar(255) |  |
| `word_resume_title` | varchar(255) |  |
| `word_resume_addtime` | int unsigned |  |
| `key_full` | text |  |
| `key_precise` | text |  |
| `click` | int unsigned |  |
| `tpl` | varchar(60) |  |
| `resume_from_pc` | tinyint unsigned |  |
| `def` | tinyint |  |
| `mobile_audit` | tinyint |  |
| `comment_content` | varchar(255) |  |
| `is_quick` | tinyint unsigned |  |
| `video` | varchar(255) |  |
| `video_audit` | tinyint |  |
| `video_display` | int |  |
| `like_num` | int | 点赞数 |
| `idcard` | varbinary(255) | 身份证号(加密) |
| `idcard_type` | tinyint | 证件类型{1身份证2外国人身份证} |
| `school_id` | int |  |
| `school_name` | varchar(50) |  |
| `school_xueyuan` | varchar(50) |  |
| `school_xi` | varchar(50) |  |
| `school_major` | varchar(50) |  |
| `school_grade` | varchar(50) | 毕业时间 |
| `school_ruxue` | varchar(32) | 入学时间 |
| `school_class_name` | varchar(255) | 所在班级 |
| `subsite_id` | int unsigned |  |
| `qz_category` | varchar(11) |  |
| `is_gx` | int |  |
| `gx_id` | int |  |
| `nation` | int | 民族ID |
| `nation_cn` | varchar(100) | 民族 |
| `politics` | int | 政治面貌ID |
| `politics_cn` | varchar(100) | 政治面貌 |
| `health` | int | 健康状况ID |
| `health_cn` | varchar(100) | 健康状况 |
| `residences` | int | 户口性质ID |
| `residences_cn` | varchar(100) | 户口性质 |
| `skill` | int | 技能等级ID |
| `skill_cn` | varchar(100) | 技能等级 |
| `stay` | int | 食宿要求ID |
| `stay_cn` | varchar(100) | 食宿要求 |
| `talentType` | int | 人才类型ID |
| `talentType_cn` | varchar(255) | 人才类型 |
| `residence_code` | varchar(50) |  |
| `sdzpw` | int | 对接山东公共招聘网 状态 0.未对接 1.已推送 2.推送失败 |

#### `qs_resume_education` 简历教育经历表（5 行 · 15 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `pid` | int unsigned |  |
| `uid` | int unsigned |  |
| `startyear` | smallint unsigned |  |
| `startmonth` | smallint unsigned |  |
| `endyear` | smallint unsigned |  |
| `endmonth` | smallint unsigned |  |
| `school` | varchar(50) |  |
| `speciality` | varchar(50) |  |
| `education` | smallint unsigned |  |
| `education_cn` | varchar(30) |  |
| `todate` | int unsigned |  |
| `campus_id` | int unsigned |  |
| `gx_id` | int |  |
| `is_gx` | int |  |

#### `qs_resume_work` 简历工作经历表（5 行 · 12 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `pid` | int unsigned |  |
| `uid` | int unsigned |  |
| `startyear` | smallint unsigned |  |
| `startmonth` | smallint unsigned |  |
| `endyear` | smallint unsigned |  |
| `endmonth` | smallint unsigned |  |
| `companyname` | varchar(50) |  |
| `jobs` | varchar(30) |  |
| `achievements` | varchar(1000) |  |
| `todate` | int unsigned |  |
| `gx_id` | int |  |

#### `qs_resume_training` 简历培训经历（5 行 · 12 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `pid` | int unsigned |  |
| `uid` | int unsigned |  |
| `startyear` | smallint unsigned |  |
| `startmonth` | smallint unsigned |  |
| `endyear` | smallint unsigned |  |
| `endmonth` | smallint unsigned |  |
| `agency` | varchar(50) |  |
| `course` | varchar(50) |  |
| `description` | varchar(1000) |  |
| `todate` | int unsigned |  |
| `gx_id` | int |  |

#### `qs_resume_language` 简历语言能力表（5 行 · 8 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `pid` | int unsigned |  |
| `uid` | int unsigned |  |
| `language` | smallint |  |
| `language_cn` | varchar(50) |  |
| `level` | smallint unsigned |  |
| `level_cn` | varchar(50) |  |
| `gx_id` | int |  |

#### `qs_resume_credent` 简历证书表（5 行 · 8 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `pid` | int unsigned |  |
| `uid` | int unsigned |  |
| `name` | varchar(255) |  |
| `year` | int |  |
| `month` | int |  |
| `images` | varchar(255) |  |
| `gx_id` | int |  |

#### `qs_resume_project` 简历项目经历表（5 行 · 12 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `pid` | int unsigned |  |
| `uid` | int unsigned |  |
| `startyear` | smallint unsigned |  |
| `startmonth` | smallint unsigned |  |
| `endyear` | smallint unsigned |  |
| `endmonth` | smallint unsigned |  |
| `projectname` | varchar(50) |  |
| `role` | varchar(50) |  |
| `description` | varchar(1000) |  |
| `todate` | int unsigned |  |
| `gx_id` | int |  |

#### `qs_members` 注册成员表（10 行 · 41 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | int unsigned |  |
| `utype` | tinyint unsigned |  |
| `username` | varchar(60) |  |
| `email` | varchar(80) |  |
| `email_audit` | tinyint unsigned |  |
| `mobile` | varchar(11) |  |
| `mobile_audit` | tinyint unsigned |  |
| `password` | varchar(100) |  |
| `pwd_hash` | varchar(30) |  |
| `reg_time` | int unsigned |  |
| `reg_ip` | varchar(30) |  |
| `reg_address` | varchar(30) |  |
| `last_login_time` | int unsigned |  |
| `last_login_ip` | varchar(30) |  |
| `status` | tinyint unsigned |  |
| `avatars` | varchar(255) |  |
| `robot` | tinyint unsigned |  |
| `consultant` | smallint unsigned |  |
| `imei` | varchar(50) |  |
| `sms_num` | int |  |
| `invitation_code` | varchar(8) |  |
| `sitegroup_uid` | int |  |
| `reg_source` | tinyint unsigned |  |
| `reg_source_cn` | varchar(30) |  |
| `secretkey` | varchar(100) |  |
| `remind_email_time` | int unsigned |  |
| `remind_email_ex_time` | tinyint unsigned |  |
| `terminal` | varchar(10) |  |
| `registration_id` | varchar(30) |  |
| `reg_type` | tinyint unsigned |  |
| `uc_uid` | int |  |
| `gx_id` | int |  |
| `is_gx` | int |  |
| `session_id` | varchar(32) | 小程序登录标识 |
| `session_time` | int | 过期时间 |
| `special` | tinyint | 特殊账号 1-平台信息发布员 2-主管领导 11.数据导出申请人 12.数据导出一级审批人 13.数据导出二级审批人 |
| `leader_uid` | int | 主管领导uid |
| `special_city` | varchar(50) | 数据申请 所在地区 |
| `visit_time` | int | 回访时间 |
| `visit_remark` | varchar(255) | 回访备注信息 |
| `isdzq` | int |  |

#### `qs_personal_jobs_apply` 个人申请职位表（10 行 · 20 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `did` | int unsigned |  |
| `resume_id` | int unsigned |  |
| `resume_name` | varchar(60) |  |
| `personal_uid` | int unsigned |  |
| `jobs_id` | int unsigned |  |
| `jobs_name` | varchar(60) |  |
| `company_id` | int unsigned |  |
| `company_name` | varchar(60) |  |
| `company_uid` | int unsigned |  |
| `apply_addtime` | int unsigned |  |
| `personal_look` | tinyint unsigned |  |
| `notes` | varchar(200) |  |
| `is_reply` | tinyint unsigned | 0-未回复，1-合适，2-不合适，3-待定，4-未接通，5-邀请入职，6-已面试、7-未入职，8-已入职其它单位，9-纳入储备库 |
| `is_apply` | tinyint |  |
| `reply_time` | int |  |
| `admin_username` | varchar(30) |  |
| `personal_look_time` | int |  |
| `personal_reply` | tinyint |  |
| `jyzx_id` | bigint |  |
| `txt` | text |  |

#### `qs_personal_favorites` 收藏职位表（5 行 · 5 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `did` | int unsigned |  |
| `personal_uid` | int unsigned |  |
| `jobs_id` | int unsigned |  |
| `jobs_name` | varchar(100) |  |
| `addtime` | int unsigned |  |

#### `qs_personal_jobs_subscribe`（4 行 · 16 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `uid` | int |  |
| `title` | varchar(100) |  |
| `key` | varchar(100) |  |
| `trade` | int |  |
| `trade_cn` | varchar(255) |  |
| `intention_jobs_id` | varchar(50) |  |
| `intention_jobs` | varchar(255) |  |
| `district` | varchar(50) |  |
| `district_cn` | varchar(255) |  |
| `wage` | int |  |
| `wage_cn` | varchar(255) |  |
| `email` | varchar(30) |  |
| `addtime` | int |  |
| `update_time` | int |  |
| `status` | tinyint |  |

#### `qs_view_jobs` 职位查看记录表（0 行 · 5 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `uid` | int unsigned |  |
| `jobs_uid` | int |  |
| `jobsid` | int unsigned |  |
| `addtime` | int unsigned |  |

#### `qs_view_resume` 简历查看记录表（0 行 · 5 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `uid` | int unsigned |  |
| `resumeid` | int unsigned |  |
| `resume_uid` | int |  |
| `addtime` | int unsigned |  |

#### `qs_register_employment` 灵活就业人员登记（5 行 · 32 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `aac001` | varchar(255) | 人员编号 |
| `aac003` | varchar(255) | 姓名 |
| `aac147` | varchar(255) | 证件号码 |
| `is_post` | int | 是否邮寄 0 否，1 是 |
| `post_type` | int | 证书邮寄方式 1邮寄，2 自取 |
| `post_name` | varchar(50) | 姓名 |
| `post_phone` | varchar(11) | 手机号 |
| `post_address` | varchar(255) | 地址 |
| `acc031` | varchar(50) | 灵活就业开始时间 |
| `bdc013` | int | 从事灵活就业类型 |
| `bdc019` | text | 灵活就业工作内容 |
| `bdc604` | varchar(255) | 灵活就业地址 |
| `bdc226` | varchar(255) | 灵活就业形式 |
| `cdc901` | varchar(255) | 人员类别 |
| `cdc902` | varchar(255) | 人员类别2类 |
| `cdc903` | varchar(255) | 人员类别3类 |
| `bdc011` | varchar(255) | 就业行业 |
| `bdc603` | varchar(255) | 岗位名称 |
| `bdc015` | varchar(255) | 每周工作时间（小时） |
| `bdc016` | varchar(255) | 个人月收入（元） |
| `bdc017` | varchar(255) | 家庭人均月收入（元） |
| `cab004` | varchar(255) | 就业单位名称 |
| `bdc020` | varchar(255) | 拟申请享受扶持政策 |
| `aab301` | varchar(255) | 就业所在区划 |
| `cac206` | varchar(255) | 就业所在社区（乡村） |
| `aab306` | varchar(255) | 就业所在街道 |
| `status` | int | 审核状态 |
| `addtime` | int | 提交时间 |
| `verifytime` | int | 审核时间 |
| `uid` | int | 用户ID |
| `adduser` | int | 基层专员用户ID |

#### `qs_register_employment_personal` 个体经营就业人员登记（5 行 · 23 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `aac001` | varchar(255) | 人员编号 |
| `aac003` | varchar(255) | 姓名 |
| `aac147` | varchar(255) | 证件号码 |
| `is_post` | int | 是否邮寄 0 否，1 是 |
| `post_type` | int | 证书邮寄方式 1邮寄，2 自取 |
| `post_name` | varchar(50) |  |
| `post_phone` | varchar(11) |  |
| `post_address` | varchar(255) |  |
| `aab009` | varchar(255) | 营业执照开始时间 |
| `aab010` | varchar(255) | 营业执照结束时间 |
| `bdc033` | varchar(255) | 工作地点 |
| `cab001` | varchar(255) | 单位编号 |
| `aab069` | varchar(255) | 个体经营名称 |
| `bdc006` | varchar(255) | 人员类别 |
| `cdc902` | varchar(255) | 人员类别2类 |
| `cdc903` | varchar(255) | 人员类别3类 |
| `aab301` | varchar(255) | 就业所在区划 |
| `cac206` | varchar(255) | 就业所在社区（乡村） |
| `aab306` | varchar(255) | 就业所在街道 |
| `status` | int | 状态 |
| `addtime` | int | 提交时间 |
| `verifytime` | int | 审核时间 |

#### `qs_register_unemployment` 失业登记（5 行 · 35 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `zone1` | varchar(255) | 所属经办机构区划 |
| `aac001` | varchar(255) | 人员编号 |
| `aac003` | varchar(255) | 人员姓名 |
| `aac147` | varchar(255) | 身份证号 |
| `cdc006` | varchar(255) | 失业原因 |
| `cdc160` | varchar(255) | 失业登记类型 |
| `cac205` | varchar(255) | 户籍登记区 |
| `aaf013x` | varchar(255) | 户籍登记街道 |
| `cac217` | varchar(255) | 常住地登记区 |
| `cac218` | varchar(255) | 常住地登记街道 |
| `cbd111` | varchar(255) | 参保地登记区 |
| `cbd112` | varchar(255) | 参保地登记街道 |
| `jyd111` | varchar(255) | 就业地登记区 |
| `jyd112` | varchar(255) | 就业地登记街道 |
| `aaf030` | varchar(255) | 社区 |
| `cdc294` | varchar(255) | 失业时间 |
| `aae815` | varchar(255) | 其他需说明的事项 |
| `adc220` | varchar(255) | 其他失业原因 |
| `adc222` | varchar(255) | 是否领取失业金 |
| `cab001` | varchar(255) | 失业前单位编号 |
| `cab004` | varchar(255) | 失业前单位名称 |
| `cdc602` | varchar(255) | 求职意向 |
| `is_post` | varchar(255) | 是否邮寄 |
| `post_type` | varchar(255) | 证书邮寄方式 |
| `post_name` | varchar(255) | 邮寄姓名 |
| `post_phone` | varchar(255) | 邮寄电话 |
| `post_address` | varchar(255) | 邮寄地址 |
| `cdc356` | varchar(255) | 是否求职 |
| `cdc011` | varchar(255) | 不求职原因 |
| `cdc291` | varchar(255) | 是否发布求职信息 |
| `cdc603` | varchar(255) | 求职工种 |
| `status` | int | 状态 |
| `addtime` | int | 提交时间 |
| `verifytime` | int | 审核时间 |

### 会话与转人工

| 表名 | 说明 | 行数 | 字段数 |
|---|---|---|---|
| `qs_consultant` |  | 0 | 6 |
| `qs_consultant_complaint` |  | 2 | 8 |
| `ly_per_touch` | 求职者沟通详情 | 5 | 13 |
| `ly_pmember_touch` | 求职者会员沟通表 | 5 | 10 |
| `ly_com_touch` | 企业沟通详情 | 5 | 13 |
| `ly_cmember_touch` | 企业会员沟通表 | 5 | 10 |
| `ly_refer` | 运营人员推荐表 | 5 | 13 |
| `ly_track_log` | 跟踪就业操作日志 | 0 | 9 |
| `ly_track_touch` | 沟通详情 | 0 | 14 |
| `qs_union_chat_record` | 用工联盟聊天记录表 | 3 | 5 |
| `qs_im_user` |  | 0 | 7 |
| `qs_im_message` |  | 0 | 7 |
| `qs_weixin_msg_list` |  | 5 | 8 |
| `qs_wechat_msg` |  | 5 | 5 |
| `qs_wechat_openid` |  | 5 | 3 |
| `qs_feedback` | 意见反馈表 | 5 | 6 |

#### `qs_consultant`（0 行 · 6 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `name` | varchar(100) |  |
| `pic` | text |  |
| `qq` | varchar(15) |  |
| `mobile` | varchar(15) |  |
| `tel` | varchar(30) |  |

#### `qs_consultant_complaint`（2 行 · 8 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `uid` | int unsigned |  |
| `username` | varchar(30) |  |
| `consultant_id` | int unsigned |  |
| `consultant_name` | varchar(30) |  |
| `notes` | text |  |
| `addtime` | int unsigned |  |
| `audit` | tinyint |  |

#### `ly_per_touch` 求职者沟通详情（5 行 · 13 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | ID |
| `uid` | int | 服务id |
| `item_id` | varchar(200) | 沟通事项 |
| `remark` | text | 详情备注 |
| `image` | varchar(255) | 图片 |
| `touch_id` | int | 沟通人id |
| `touch_time` | int | 沟通时间 |
| `touch_name` | varchar(30) | 沟通人 |
| `tou_info` | text | 招聘会后-招聘情况 |
| `refer_ids` | varchar(300) | 推荐表id |
| `ref_info` | text | 推荐求职者信息 |
| `status` | tinyint | 状态：0正常\|1待审核 |
| `add_time` | int | 上传时间 |

#### `ly_pmember_touch` 求职者会员沟通表（5 行 · 10 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `uid` | int | 服务对象id |
| `item_id` | int | 沟通事项 |
| `image` | varchar(255) | 图片 |
| `remark` | varchar(255) | 沟通备注 |
| `touch_id` | int | 沟通人id |
| `touch_time` | int | 沟通时间 |
| `touch_name` | varchar(30) | 沟通人 |
| `status` | tinyint | 状态：0正常\|1待审核 |
| `add_time` | int | 上传时间 |

#### `ly_com_touch` 企业沟通详情（5 行 · 13 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | ID |
| `uid` | int | 服务id |
| `item_id` | varchar(200) | 沟通事项 |
| `remark` | text | 详情备注 |
| `image` | varchar(255) | 图片 |
| `touch_id` | int | 沟通人id |
| `touch_time` | int | 沟通时间 |
| `touch_name` | varchar(30) | 沟通人 |
| `tou_info` | text | 招聘会后-招聘情况 |
| `refer_ids` | varchar(300) | 推荐表id |
| `ref_info` | text | 推荐求职者信息 |
| `status` | tinyint | 状态：0正常\|1待审核 |
| `add_time` | int | 上传时间 |

#### `ly_cmember_touch` 企业会员沟通表（5 行 · 10 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `uid` | int | 服务对象id |
| `item_id` | int | 沟通事项 |
| `image` | varchar(255) | 图片 |
| `remark` | varchar(255) | 沟通备注 |
| `touch_id` | int | 沟通人id |
| `touch_time` | int | 沟通时间 |
| `touch_name` | varchar(30) | 沟通人 |
| `status` | tinyint | 状态：0正常\|1待审核 |
| `add_time` | int | 上传时间 |

#### `ly_refer` 运营人员推荐表（5 行 · 13 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | ID |
| `uid` | int | 当前企业或个人uid |
| `utype` | tinyint | 当前企业或个人类型{1企业\|2个人} |
| `refer_id` | int | 简历id或企业id |
| `refer_uid` | int | 求职者或企业uid |
| `refer_name` | varchar(100) | 推荐(求职者或企业) |
| `refer_mobile` | varchar(30) | 推荐手机号 |
| `jobs_id` | int | 推荐职位id |
| `jobs_name` | varchar(50) | 推荐职位 |
| `add_time` | int | 添加时间 |
| `track_id` | int |  |
| `track_name` | varchar(30) |  |
| `is_touch` | tinyint | 是否加入沟通记录 |

#### `ly_track_log` 跟踪就业操作日志（0 行 · 9 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | ID |
| `uid` | int |  |
| `utype` | tinyint | 类型{1企业,2个人} |
| `category` | tinyint | 类型{1推荐求职者,2推荐企业,3处理投诉,4填写沟通详情} |
| `detail` | varchar(255) | 详情 |
| `track_id` | int | 操作人id |
| `track_name` | varchar(50) | 操作人 |
| `track_ip` | varchar(30) | 操作IP |
| `add_time` | int | 操作时间 |

#### `ly_track_touch` 沟通详情（0 行 · 14 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | ID |
| `uid` | int | 服务id |
| `utype` | tinyint | 类型{1企业\|2个人} |
| `item_id` | varchar(20) | 沟通事项 |
| `remark` | text | 详情备注 |
| `image` | varchar(255) | 图片 |
| `refer_ids` | varchar(300) | 推荐表id |
| `touch_id` | int | 沟通id |
| `touch_time` | int | 沟通时间 |
| `touch_name` | varchar(30) | 沟通人 |
| `status` | tinyint | 状态：0正常\|1待审核\|2被驳回\|3已删除 |
| `add_time` | int | 上传时间 |
| `del_time` | int | 请求删除时间 |
| `audit_deltime` | int | 审核删除时间 |

#### `qs_union_chat_record` 用工联盟聊天记录表（3 行 · 5 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned | ID |
| `gid` | int | 群ID |
| `content` | varchar(500) | 内容 |
| `uid` | int | 发送者ID |
| `create_time` | int | 创建时间 |

#### `qs_im_user`（0 行 · 7 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `formuid` | int unsigned |  |
| `touid` | int |  |
| `addtime` | int |  |
| `message` | text |  |
| `sendtime` | int |  |
| `unread` | int |  |

#### `qs_im_message`（0 行 · 7 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `ftid` | int unsigned |  |
| `formuid` | int unsigned |  |
| `touid` | int unsigned |  |
| `message` | text |  |
| `addtime` | int unsigned |  |
| `mutually` | tinyint unsigned |  |

#### `qs_weixin_msg_list`（5 行 · 8 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `uid` | int |  |
| `utype` | tinyint unsigned |  |
| `username` | varchar(60) |  |
| `weixin_openid` | varchar(50) |  |
| `content` | text |  |
| `sendtime` | int unsigned |  |
| `status` | tinyint unsigned |  |

#### `qs_wechat_msg`（5 行 · 5 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `key` | varchar(255) |  |
| `content` | text |  |
| `type` | varchar(255) |  |
| `json` | longtext |  |

#### `qs_wechat_openid`（5 行 · 3 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `openid` | varchar(255) |  |
| `status` | int |  |

#### `qs_feedback` 意见反馈表（5 行 · 6 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `infotype` | tinyint unsigned |  |
| `feedback` | varchar(250) |  |
| `addtime` | int |  |
| `tel` | varchar(255) |  |
| `audit` | tinyint |  |

小计：53 张表。

## gt_sccy（水城创业网·双创平台）

### 用户与角色

| 表名 | 说明 | 行数 | 字段数 |
|---|---|---|---|
| `zc_member` |  | 10 | 41 |
| `zc_member_detail` |  | 5 | 15 |
| `zc_member_cate` |  | 9 | 3 |
| `zc_member_weixin` |  | 5 | 7 |
| `zc_member_verify` |  | 5 | 3 |

#### `zc_member`（10 行 · 41 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | int unsigned |  |
| `name` | varchar(50) |  |
| `passwd` | char(32) |  |
| `from` | enum('member','teacher','partners','investors','shop','temporary','incubator') |  |
| `yq_id` | int | 园区ID |
| `mail` | varchar(100) | 邮箱 |
| `mobile` | varchar(15) | 手机 |
| `cate_id` | mediumint | 角色 |
| `gold` | int | 积分 |
| `price` | int |  |
| `gender` | enum('man','woman') | 性别 |
| `city` | varchar(100) | 所在城市 |
| `nickname` | varchar(50) | 昵称 |
| `face` | varchar(150) |  |
| `Y` | smallint | 生日年 |
| `M` | tinyint | 生日月 |
| `D` | tinyint | 生日日 |
| `verify` | tinyint | 0待验证 1 邮箱验证通过 2是手机验证通过 3是s手机邮箱都通过 -2 手机认证失败 -1 邮箱验证失败 4是实名验证通过 7 全部都通过 |
| `lastlogin` | int unsigned | 最后登录时间 |
| `loginip` | char(15) | 最后登录IP |
| `follow_num` | smallint |  |
| `yuyue_count` | smallint |  |
| `comments` | smallint | 评论数 |
| `scores` | mediumint | 评论分值 |
| `fenxiao_id` | mediumint |  |
| `projects` | mediumint |  |
| `zhong_projects` | mediumint |  |
| `tou_projects` | mediumint |  |
| `tou_price` | int |  |
| `dong_price` | int |  |
| `is_company` | tinyint |  |
| `views` | int |  |
| `closed` | tinyint unsigned | 0:正常,1:禁言,2:锁定,3:删除 |
| `regip` | char(15) | 注册IP |
| `zhuanye` | tinyint |  |
| `fanwei` | tinyint |  |
| `dateline` | int unsigned | 注册时间 |
| `from_type` | int | 注册类型 1.主站 2.临清分站 |
| `tj` | int |  |
| `session_id` | char(255) | session |
| `session_time` | int | session过期时间 |

#### `zc_member_detail`（5 行 · 15 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | int |  |
| `realname` | varchar(20) |  |
| `card` | varchar(20) |  |
| `card_zhen` | varchar(150) |  |
| `card_fan` | varchar(150) |  |
| `target_industry` | varchar(200) | 关注行业 |
| `yulu` | text | 语录 |
| `desc` | text | 描述 |
| `personal_addr` | varchar(150) | 个人所在园区具体地址 |
| `company_addr` | varchar(500) | 性格 |
| `company_hangye` | varchar(50) |  |
| `company_name` | varchar(200) |  |
| `company_photo` | varchar(200) |  |
| `dateline` | int unsigned | 注册时间 |
| `qw_time` | varchar(20) | 期望入住时间 |

#### `zc_member_cate`（9 行 · 3 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `cate_id` | int unsigned |  |
| `cate_name` | char(32) |  |
| `dateline` | int unsigned | 注册时间 |

#### `zc_member_weixin`（5 行 · 7 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `wx_id` | mediumint |  |
| `uid` | int |  |
| `openid` | varchar(200) |  |
| `nickname` | varchar(200) |  |
| `img` | varchar(200) |  |
| `unionid` | varchar(50) |  |
| `dateline` | int |  |

#### `zc_member_verify`（5 行 · 3 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | mediumint |  |
| `audit` | tinyint |  |
| `dateline` | int |  |

### 会话与人工专家（转人工）

| 表名 | 说明 | 行数 | 字段数 |
|---|---|---|---|
| `zc_chat` |  | 5 | 7 |
| `zc_teacher_detail` |  | 5 | 15 |
| `zc_teacher_cate` |  | 24 | 4 |
| `zc_expert_meet` | 导师评审表 | 3 | 11 |

#### `zc_chat`（5 行 · 7 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `text` | text |  |
| `user_id` | int |  |
| `teacher_id` | int |  |
| `time` | int |  |
| `position` | int | 1.左 2.右 |
| `status` | int | 0.未读 1.已读 |

#### `zc_teacher_detail`（5 行 · 15 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | int |  |
| `realname` | varchar(20) |  |
| `card` | varchar(20) |  |
| `card_zhen` | varchar(150) |  |
| `card_fan` | varchar(150) |  |
| `target_industry` | varchar(200) | 关注行业 |
| `yulu` | text | 语录 |
| `desc` | text | 描述 |
| `company_addr` | varchar(500) | 性格 |
| `company_hangye` | varchar(50) |  |
| `company_name` | varchar(200) |  |
| `company_photo` | varchar(200) |  |
| `dateline` | int unsigned | 注册时间 |
| `images` | text |  |
| `video` | varchar(255) |  |

#### `zc_teacher_cate`（24 行 · 4 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `cate_id` | int unsigned |  |
| `title` | char(32) |  |
| `orderby` | int |  |
| `dateline` | int unsigned | 注册时间 |

#### `zc_expert_meet` 导师评审表（3 行 · 11 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `expert_uid` | int |  |
| `activity_id` | int |  |
| `username` | varchar(12) |  |
| `truename` | varchar(12) |  |
| `telephone` | varchar(15) |  |
| `activity_` | int |  |
| `activity_name` | varchar(40) |  |
| `status` | int |  |
| `start_time` | varchar(10) |  |
| `end_time` | varchar(10) |  |

### 政策与创业内容（政策咨询知识源）

| 表名 | 说明 | 行数 | 字段数 |
|---|---|---|---|
| `zc_article` |  | 10 | 15 |
| `zc_article_cate` |  | 13 | 8 |
| `zc_fxcytjb` |  | 5 | 26 |
| `zc_zoufang` |  | 5 | 58 |
| `zc_zoufang_lizhi` |  | 5 | 9 |
| `zc_zoufang_xina` |  | 5 | 11 |

#### `zc_article`（10 行 · 15 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `article_id` | int unsigned |  |
| `cate_id` | mediumint unsigned | 分类ID |
| `from` | enum('article','footer','help') | 内容类型 |
| `title` | varchar(200) | 标题 |
| `thumb` | varchar(150) | 缩略图 |
| `desc` | varchar(500) | 描述 |
| `details` | longtext |  |
| `views` | mediumint | 查看数 |
| `linkurl` | varchar(255) | 连接地址 |
| `orderby` | smallint unsigned | 排序 |
| `audit` | tinyint | 审核,1:发布，2:待审 |
| `comments` | mediumint |  |
| `score` | mediumint |  |
| `closed` | tinyint unsigned |  |
| `dateline` | int unsigned |  |

#### `zc_article_cate`（13 行 · 8 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `cate_id` | mediumint unsigned |  |
| `parent_id` | mediumint unsigned |  |
| `title` | varchar(150) |  |
| `level` | tinyint unsigned |  |
| `from` | enum('about','help','page','article') |  |
| `orderby` | smallint unsigned |  |
| `hidden` | tinyint | 1:隐藏,0:不隐藏 |
| `dateline` | int unsigned |  |

#### `zc_fxcytjb`（5 行 · 26 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `type` | tinyint | 2.返乡创业人员 |
| `working` | varchar(300) |  |
| `unit_nature` | varchar(100) | 工作单位性质 |
| `project_area` | varchar(100) | 打算返乡创业的项目或领域 |
| `amount` | varchar(32) | 返乡创业投资的额度 |
| `cy_area` | varchar(100) | 在哪创业 |
| `inp_txt2` | varchar(100) | 创业政策和创业服务方面有什么需求 |
| `rc_name` | varchar(32) | 姓名 |
| `sex` | int | 性别 |
| `rc_tell` | varchar(32) | 手机号码 |
| `rc_code` | varchar(32) | 身份证号 |
| `rc_education` | varchar(32) | 学历 |
| `rc_status` | varchar(32) | 政治面貌 |
| `rc_county` | varchar(32) | 县（市/区) |
| `rc_town` | varchar(32) | 乡镇/街道 |
| `rc_village` | varchar(32) | 村/社区 |
| `job_title` | varchar(32) | 职称 |
| `y_province` | varchar(32) | 省 |
| `y_city` | varchar(32) | 城市 |
| `y_county` | varchar(32) | 县/市/区 |
| `y_employer` | varchar(50) | 原工作单位 |
| `y_position` | varchar(32) | 原职务 |
| `income_2018` | varchar(32) | 2018年营业收入 |
| `policy_demand` | varchar(500) | 创业政策需求 |
| `city_good` | varchar(200) | 现工作城市好的创业政策或项目 |

#### `zc_zoufang`（5 行 · 58 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `unit_name` | varchar(255) |  |
| `unit_room` | varchar(255) |  |
| `unit_lead` | varchar(255) |  |
| `unit_tell` | varchar(255) |  |
| `zfry_name` | varchar(255) | 走访人员 |
| `sq_year` | varchar(255) | 年 |
| `sq_month` | varchar(255) | 月 |
| `sq_day` | varchar(255) | 日 |
| `monthly_turnover` | varchar(255) | 月营业额 |
| `monthly_tax` | varchar(255) | 月利税 |
| `funds_needs` | varchar(255) | 资金融资需求 |
| `zdfw_num1` | varchar(255) | 企业是否需要协助办理工商注册、银行开户、税务登记等公司开办手续 |
| `zdfw_bz1` | varchar(255) | 备注 |
| `zdfw_num2` | varchar(255) | 创业指导服务 |
| `zdfw_bz2` | varchar(255) | 备注 |
| `zdfw_num3` | varchar(255) | 法律咨询服务； |
| `zdfw_bz3` | varchar(255) | 备注 |
| `zdfw_num4` | varchar(255) | 协助办理知识产权申请服务； |
| `zdfw_bz4` | varchar(255) | 备注 |
| `zdfw_num5` | varchar(255) | 企业是否需要协助进行档案管理服务； |
| `zdfw_bz5` | varchar(255) | 备注 |
| `zdfw_num6` | varchar(255) | 对接社保咨询、政策讲解，对接三方服务机构等提供协助业务； |
| `zdfw_bz6` | varchar(255) | 备注 |
| `zdfw_num7` | varchar(255) | 企业是否需要投融资路演活动文件前期指导工作，企业股权、基金等问题的咨询讲解； |
| `zdfw_bz7` | varchar(255) | 备注 |
| `zdfw_num8` | varchar(255) | 企业是否需要通过创业孵化基地运营信息平台“水城创业网”线上申请服务，解决企业绝大部分疑问及诉求 |
| `zdfw_bz8` | varchar(255) | 备注 |
| `zdfw_num9` | varchar(255) | 企业是否需要协助申请创业担保贷款相关手续； |
| `zdfw_bz9` | varchar(255) | 备注 |
| `zdfw_num10` | varchar(255) | 企业是否需要协助申请就业见习补贴； |
| `zdfw_bz10` | varchar(255) | 备注 |
| `zdfw_num11` | varchar(255) | 企业是否需要协助办理职工就业登记备案、社保参保手续等服务； |
| `zdfw_bz11` | varchar(255) | 备注 |
| `ysqfw_num1` | varchar(255) | 专业法律顾问提供服务 |
| `ysqfw_textarea1` | varchar(255) | 选项 |
| `ysqfw_bz1` | varchar(255) | 备注 |
| `ysqfw_num2` | varchar(255) | 知识产权服务 |
| `ysqfw_textarea2` | varchar(255) | 选项 |
| `ysqfw_bz2` | varchar(255) | 备注 |
| `ysqfw_num3` | varchar(255) | 人力资源管理服务 |
| `ysqfw_textarea3` | varchar(255) | 选项 |
| `ysqfw_bz3` | varchar(255) | 备注 |
| `ysqfw_num4` | varchar(255) | 财税审计服务 |
| `ysqfw_textarea4` | varchar(255) | 选项 |
| `ysqfw_bz4` | varchar(255) | 备注 |
| `ysqfw_num5` | varchar(255) | 资本对接服务 |
| `ysqfw_textarea5` | varchar(255) | 选项 |
| `ysqfw_bz5` | varchar(255) | 备注 |
| `other_needs` | text | 企业其他需求服务，企业发展急需解决的其他问题 |
| `luoshi_happening` | text | 企业提出的服务与问题，孵化基地填写落实情况 |
| `opinion_box` | text | 企业对于孵化基地工作落实情况，做出评价 |
| `fhjd_pingjia` | varchar(255) | 评价 |
| `unit_leadName` | varchar(255) | 负责人签字 |
| `write_date` | varchar(255) | 签字日期 |
| `uid` | int | 用户ID |
| `addtime` | int | 添加时间 |
| `status` | int | 状态 |

#### `zc_zoufang_lizhi`（5 行 · 9 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `member_name2` | varchar(255) |  |
| `member_sex2` | varchar(255) |  |
| `member_age2` | varchar(255) |  |
| `member_edu2` | varchar(255) |  |
| `member_position2` | varchar(255) |  |
| `member_recruitTime2` | varchar(255) |  |
| `member_signature2` | varchar(255) |  |
| `pid` | int |  |

#### `zc_zoufang_xina`（5 行 · 11 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `member_name` | varchar(255) |  |
| `member_sex` | varchar(255) |  |
| `member_age` | varchar(255) |  |
| `member_edu` | varchar(255) |  |
| `member_position` | varchar(255) |  |
| `member_recruitTime` | varchar(255) |  |
| `member_contract` | varchar(255) |  |
| `member_shebao` | varchar(255) |  |
| `member_signature` | varchar(255) |  |
| `pid` | int |  |

### 创业业务数据

| 表名 | 说明 | 行数 | 字段数 |
|---|---|---|---|
| `zc_project` | 创业项目表 | 10 | 36 |
| `zc_project_cate` | 项目分类表 | 13 | 4 |
| `zc_project_comment` |  | 5 | 9 |
| `zc_project_follow` |  | 5 | 4 |
| `zc_project_look` |  | 5 | 10 |
| `zc_cydbdk` |  | 5 | 7 |
| `zc_settle_grsqb` |  | 5 | 59 |
| `zc_settle_qysqb` |  | 5 | 60 |
| `zc_hmc` |  | 5 | 10 |
| `zc_hmc_member` |  | 5 | 14 |
| `zc_ruzhu_info` |  | 5 | 13 |
| `zc_incubator` |  | 4 | 18 |
| `zc_incubator_service` |  | 5 | 3 |
| `zc_activity` |  | 10 | 21 |
| `zc_activity_sign` |  | 3 | 10 |
| `zc_activity_submit` |  | 5 | 10 |
| `zc_feedback` |  | 5 | 8 |

#### `zc_project` 创业项目表（10 行 · 36 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `project_id` | mediumint |  |
| `uid` | mediumint |  |
| `title` | varchar(200) |  |
| `yq_id` | int | 园区ID 0.未选择 |
| `name` | varchar(50) |  |
| `mobile` | varchar(15) |  |
| `type` | enum('crowd','investors','partners') | 1是找伯乐 2是找合伙人 3是众筹 4是找创意 |
| `city` | varchar(50) |  |
| `cate_id` | mediumint |  |
| `ltime` | int | 截止时间 |
| `investment` | int | 需要投资(找伯乐) |
| `huibao` | tinyint | 回报方式: 1是股权 2是分红 3是奖励回报 4是梦想支持 |
| `proposal` | varchar(300) | 企划书 |
| `video` | varchar(300) | 视频介绍 |
| `xieyi` | varchar(300) | 合伙协议 |
| `desc` | text |  |
| `content` | text |  |
| `days` | smallint | 多少天寄合同 |
| `fund_raising` | int | 计划集资（众筹） |
| `project_photo` | varchar(300) |  |
| `crowd_photo` | varchar(300) |  |
| `audit` | tinyint |  |
| `gold` | smallint |  |
| `gold2` | smallint |  |
| `comments` | smallint |  |
| `scores` | int |  |
| `follows` | smallint |  |
| `views` | int |  |
| `status` | tinyint | 0是发布成功 1 招标中（筹资中）2 中标（筹资完成） 3 伯乐已支付尾款 4成功 -1是过期 |
| `dingjin` | int |  |
| `have_num` | mediumint | 众筹使用(当前人数) |
| `have_price` | int | 众筹使用(当前金额) |
| `closed` | tinyint |  |
| `your_jiese` | mediumint |  |
| `clientip` | varchar(20) |  |
| `dateline` | int |  |

#### `zc_project_cate` 项目分类表（13 行 · 4 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `cate_id` | mediumint |  |
| `cate_name` | varchar(100) |  |
| `orderby` | smallint |  |
| `dateline` | int |  |

#### `zc_project_comment`（5 行 · 9 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `comment_id` | int unsigned |  |
| `project_id` | mediumint unsigned |  |
| `uid` | mediumint unsigned | 评论UID |
| `chuangyi_id` | int |  |
| `score` | tinyint unsigned | 评分(1~5) |
| `content` | varchar(255) | 评论内容 |
| `audit` | tinyint |  |
| `clientip` | varchar(15) |  |
| `dateline` | int |  |

#### `zc_project_follow`（5 行 · 4 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `follow_id` | mediumint |  |
| `uid` | mediumint |  |
| `project` | mediumint |  |
| `dateline` | int |  |

#### `zc_project_look`（5 行 · 10 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `look_id` | int unsigned |  |
| `project_id` | mediumint |  |
| `city` | varchar(30) |  |
| `uid` | mediumint |  |
| `is_chakan` | tinyint |  |
| `is_signed` | tinyint |  |
| `sign_time` | int |  |
| `content` | varchar(255) |  |
| `clientip` | varchar(15) |  |
| `dateline` | int |  |

#### `zc_cydbdk`（5 行 · 7 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `xw_name` | varchar(32) | 小微企业（个人）名称 |
| `contact` | varchar(32) | 联系人 |
| `con_tell` | varchar(32) | 联系电话 |
| `loan_object` | varchar(32) | 贷款对象 |
| `sqdk_amount` | varchar(32) | 申请贷款额度 |
| `time` | varchar(32) | 申请时间 |

#### `zc_settle_grsqb`（5 行 · 59 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `sq_date` | varchar(255) | 申请日期 |
| `cyr_name` | varchar(255) | 创业人姓名 |
| `birth_date` | varchar(255) | 出生年月 |
| `cyr_sex` | varchar(255) | 性别 |
| `cyr_nation` | varchar(255) | 民族 |
| `political_status2` | varchar(255) | 政治面貌 |
| `card_num` | varchar(255) | 身份证号码 |
| `cyr_tell` | varchar(255) | 联系电话 |
| `project_name` | varchar(255) | 创业项目名称 |
| `huji_address` | varchar(255) | 户籍所在地 |
| `cyr_education` | varchar(255) | 学历 |
| `graduation_time2` | varchar(255) | 毕业时间 |
| `incubation_area` | varchar(10) | 孵化区 |
| `nursery_area` | varchar(10) | 苗圃区 |
| `member_text` | text | 管理团队其他主要成员情况 |
| `qiye_name` | varchar(255) |  |
| `sources_funds` | varchar(50) | 资金来源 |
| `need_moneyNum` | varchar(255) | 所需资金 |
| `cycy_num` | varchar(255) | 参与创业人数 |
| `manage_range` | text | 经营范围 |
| `project_info` | text | 创业项目简况 |
| `expected_index` | text | 项目预计可实现的经济指标-del |
| `xina_numPlan` | varchar(255) | 拟吸纳就业人数 |
| `shebao_numPlan` | varchar(255) | 拟缴纳社保人数 |
| `signature_31` | varchar(255) | 负责人签字 |
| `signature_31_date` | varchar(255) | 签字时间 |
| `average_score` | varchar(255) | 平均综合得分 |
| `opinion_box` | varchar(255) | 意见 |
| `signature_32` | varchar(255) | 专家组组长签字 |
| `signature_32_date` | varchar(255) | 专家组组长签字时间 |
| `signature_33` | varchar(255) | 负责人签字 |
| `signature_33_date` | varchar(255) | 负责人签字时间 |
| `status` | int | 状态 |
| `time` | int | 提交表单时间 |
| `uid` | int |  |
| `sr_moneyNum1` | varchar(255) | 第一年度 |
| `srNum1_1` | varchar(255) | 第一年度 第一季度 |
| `srNum1_2` | varchar(255) |  |
| `srNum1_3` | varchar(255) |  |
| `srNum1_4` | varchar(255) |  |
| `sr_moneyNum2` | varchar(255) | 第二年度 |
| `srNum2_1` | varchar(255) |  |
| `srNum2_2` | varchar(255) |  |
| `srNum2_3` | varchar(255) |  |
| `srNum2_4` | varchar(255) |  |
| `sr_moneyNum3` | varchar(255) |  |
| `srNum3_1` | varchar(255) |  |
| `srNum3_2` | varchar(255) |  |
| `srNum3_3` | varchar(255) |  |
| `srNum3_4` | varchar(255) |  |
| `lr_moneyNum1` | varchar(255) |  |
| `lr_moneyNum2` | varchar(255) |  |
| `lr_moneyNum3` | varchar(255) |  |
| `entre_type` | varchar(255) | 创业类型 |
| `chanye_type` | varchar(255) | 产业类型 |
| `qiye_type` | varchar(255) | 企业类型 |
| `cylx_otherTxt` | varchar(255) | 创业类型——其他 |
| `qylx_otherTxt` | varchar(255) | 企业类型——其他 |

#### `zc_settle_qysqb`（5 行 · 60 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `sq_date` | varchar(10) | 申请表 日期 |
| `qiye_name` | varchar(50) | 企业名称 |
| `faren_daibiao` | varchar(50) | 法人代表 |
| `sq_area` | varchar(50) | 申请办公面积 |
| `ruzhu_num` | varchar(50) | 入驻人数 |
| `qiye_tell` | varchar(50) | 联系方式 |
| `zhuce_time` | varchar(50) | 注册时间 |
| `zhuce_address` | varchar(50) | 注册地址 |
| `jy_type` | varchar(50) | 经营类型 |
| `zhuce_capital` | varchar(50) | 注册资本 |
| `project_total` | varchar(50) | 项目总投资 |
| `yuji_yearMoney` | varchar(50) | 预计本年度营业额 |
| `fazhan_stage` | varchar(50) | 产品（项目）发展阶段 |
| `patent_zgrz` | text | 企业现有专利或资格认证情况 |
| `win_support` | text | 企业曾获奖励或扶持情况 |
| `problem_demand` | text | 企业目前存在问题及需求 |
| `member_text` | text | 投资者以及企业团队主要成员情况 |
| `sources_funds` | varchar(50) | 资金来源 |
| `need_moneyNum` | varchar(50) | 所需资金额度 |
| `manage_range` | text | 经营范围 |
| `project_info` | text | 项目简介 |
| `sr_moneyNum1` | varchar(50) | 营业收入：第一年度(万元) |
| `sr_moneyNum2` | varchar(50) | 第二年度(万元) |
| `sr_moneyNum3` | varchar(50) | 第三年度(万元) |
| `lr_moneyNum1` | varchar(50) | 利润：第一年度(万元) |
| `lr_moneyNum2` | varchar(50) | 第二年度(万元) |
| `lr_moneyNum3` | varchar(50) | 第三年度(万元) |
| `xina_numPlan` | varchar(50) | 拟吸纳就业人数 |
| `shebao_numPlan` | varchar(50) | 拟缴纳社保人数 |
| `signature_31` | varchar(50) | 负责人签字 |
| `signature_31_date` | varchar(255) | 创业企业承诺时间 |
| `average_score` | varchar(50) | 平均综合得分 |
| `opinion_box` | varchar(50) | 意见 |
| `signature_32` | varchar(50) | 专家组组长签字 |
| `signature_32_date` | varchar(50) | 专家意见时间 |
| `signature_33` | varchar(50) | 基地管理部门意见 - 负责人签字 |
| `signature_33_date` | varchar(50) | 基地管理部门意见 时间 |
| `uid` | int |  |
| `status` | int |  |
| `srNum1_1` | varchar(255) |  |
| `srNum1_2` | varchar(255) |  |
| `srNum1_3` | varchar(255) |  |
| `srNum1_4` | varchar(255) |  |
| `srNum2_1` | varchar(255) |  |
| `srNum2_2` | varchar(255) |  |
| `srNum2_3` | varchar(255) |  |
| `srNum2_4` | varchar(255) |  |
| `srNum3_1` | varchar(255) |  |
| `srNum3_2` | varchar(255) |  |
| `srNum3_3` | varchar(255) |  |
| `srNum3_4` | varchar(255) |  |
| `entre_type` | varchar(255) |  |
| `zhengming1` | varchar(255) |  |
| `zhengming2` | varchar(255) |  |
| `cylx_otherTxt2` | varchar(255) | 创业类型——其他 |
| `chanye_type` | varchar(255) | 产业类型 |
| `qiye_type` | varchar(255) | 企业类型 |
| `qylx_otherTxt2` | varchar(255) | 企业类型——其他 |
| `project_name` | varchar(255) | 项目名称 |

#### `zc_hmc`（5 行 · 10 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `company_name` | varchar(255) |  |
| `cy_type` | varchar(255) |  |
| `memberid` | varchar(255) |  |
| `hmc_formFiller` | varchar(255) |  |
| `hmc_tell` | varchar(255) |  |
| `hmc_date` | varchar(255) |  |
| `time` | int |  |
| `uid` | int |  |
| `type` | int | 1.个人 2.企业 |

#### `zc_hmc_member`（5 行 · 14 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int |  |
| `member_name2` | varchar(255) |  |
| `member_sex` | varchar(255) |  |
| `member_birth` | varchar(255) |  |
| `member_cardNum` | varchar(255) |  |
| `member_post` | varchar(255) |  |
| `member_tell` | varchar(255) |  |
| `political_status` | varchar(255) |  |
| `entry_time` | varchar(255) |  |
| `contract_if` | varchar(255) |  |
| `member_education` | varchar(255) |  |
| `graduated_school` | varchar(255) |  |
| `graduation_time` | varchar(255) |  |
| `uid` | int |  |

#### `zc_ruzhu_info`（5 行 · 13 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `base_name` | varchar(50) | 园区名称 |
| `type` | int |  |
| `base_address` | varchar(50) |  |
| `base_fzr` | varchar(30) | 负责人 |
| `fzr_tell` | varchar(12) |  |
| `base_startTime` | varchar(20) | 启用时间 |
| `leijishiti_num` | varchar(10) | 累计在孵(入驻)实体数量 |
| `leijijiuye_num` | varchar(10) | 累计带动就业人数 |
| `lishui_num` | varchar(10) | 利税（单位：万元） |
| `xianshiti_num` | varchar(10) | 现入驻实体数量 |
| `xianjiuye_num` | varchar(10) | xianjiuye_num |
| `time` | varchar(20) |  |

#### `zc_incubator`（4 行 · 18 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | mediumint unsigned |  |
| `uid` | int | 添加人ID |
| `city_id` | smallint |  |
| `name` | varchar(50) |  |
| `detail` | varchar(5000) | 基地介绍 |
| `service_content` | varchar(100) | 服务内容 |
| `thumb` | varchar(200) | 园区缩略图 |
| `address` | varchar(100) |  |
| `service_amount` | int | 服务人员数量 |
| `require` | varchar(500) | 入住条件 |
| `see` | int | 浏览量 |
| `area` | int | 占地面积 |
| `entity` | int | 可容纳实体个数 |
| `type` | varchar(32) | 基地类型 |
| `employment` | int | 从业人员数量 |
| `dateline` | int |  |
| `orderby` | smallint | 排序 |
| `display` | tinyint |  |

#### `zc_incubator_service`（5 行 · 3 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `service_content` | varchar(50) | 服务内容 |
| `display` | int |  |

#### `zc_activity`（10 行 · 21 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `activity_id` | mediumint unsigned |  |
| `title` | varchar(128) | 标题 |
| `city` | varchar(50) | 城市ID |
| `cate_id` | smallint |  |
| `thumb` | varchar(150) | 缩略图 |
| `banner` | varchar(150) | banner |
| `phone` | varchar(30) | 电话 |
| `qq` | varchar(30) | 咨询QQ |
| `addr` | varchar(255) | 地址 |
| `bmxz` | text |  |
| `end_sign` | int | 报名截止日期 |
| `sign_num` | mediumint | 报名人数 |
| `views` | mediumint | 浏览数 |
| `lng` | float(10,6) | 地图经度 |
| `lat` | float(10,6) | 地图维度 |
| `sj` | int |  |
| `info` | mediumtext | 活动介绍 |
| `orderby` | smallint | 排序 |
| `audit` | tinyint |  |
| `clientip` | varchar(15) |  |
| `dateline` | int |  |

#### `zc_activity_sign`（3 行 · 10 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `sign_id` | int unsigned |  |
| `activity_id` | mediumint |  |
| `uid` | mediumint |  |
| `contact` | varchar(20) | 联系人 |
| `mobile` | varchar(20) | 联系电话 |
| `num` | smallint | 人数 |
| `note` | varchar(255) | 备注 |
| `status` | tinyint |  |
| `clientip` | varchar(15) |  |
| `dateline` | int |  |

#### `zc_activity_submit`（5 行 · 10 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int unsigned |  |
| `companyname` | varchar(50) |  |
| `realname` | varchar(20) |  |
| `mobile` | varchar(20) | 联系人 |
| `attendnum` | varchar(20) | 联系电话 |
| `comaddress` | varchar(50) | 人数 |
| `comscale` | varchar(100) | 备注 |
| `industry` | varchar(20) |  |
| `time` | varchar(20) |  |
| `activity_id` | int |  |

#### `zc_feedback`（5 行 · 8 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `feedback_id` | mediumint |  |
| `uid` | mediumint |  |
| `name` | varchar(50) |  |
| `mobile` | varchar(20) |  |
| `title` | varchar(200) |  |
| `content` | text |  |
| `status` | tinyint |  |
| `dateline` | int |  |

小计：32 张表。

## 两库关联与缺口

- **无共享主键**：两套独立系统，自然人关联靠手机号（`zc_member.mobile` ↔ `qs_members.mobile` / `qs_resume.telephone`）或身份证（`zc_settle_grsqb.card_num` ↔ `qs_resume.idcard`，后者已加密存储）。
- **内容互补**：gt_lyjy 覆盖岗位/求职/就业指导；gt_sccy 覆盖创业政策/担保贷款/孵化基地；`zc_zoufang` 的 11 个服务项可作智能体咨询类目枚举。
- **缺口（需新建）**：机器人会话表、职业意向/技能短板回流画像表两库均无；`qs_consultant` 人工客服配置为空表；`zc_chat` 仅导师聊天无机器人会话。
- **运行时能力**：语音与方言识别为运行时能力，库中无对应数据。
